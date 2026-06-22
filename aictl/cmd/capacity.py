"""aictl capacity — how far can you push your GPU?

`aictl fit` answers a *binary* question: "will this model fit at a context and
concurrency I have to guess?". This command answers the *inverse*, the question
users actually binary-search `fit` by hand to find:

  - What is the MAXIMUM context length I can run (at a given concurrency)?
  - How many CONCURRENT requests can I serve (at a given context)?

It solves the same VRAM = weights + KV-cache + overhead budget that `fit`
evaluates — reusing fit's exact math (single source of truth) — but rearranged to
solve for the KV-cache dimension instead of checking a fixed point.

  aictl capacity llama3.1:8b --gpu "RTX 4090"
  aictl capacity qwen3:7b --gpu auto --context 32768   # max concurrency @ 32k
  aictl capacity llama3.1:70b --gpu H100 --quant awq --json
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from typing import Any

from aictl.core.output import ok, warn, err, print_json
from aictl.core.argtypes import positive_int
from aictl.cmd.fit import (
    GPU_VRAM_MB,
    QUANT_CONFIGS,
    VRAM_SAFETY,
    OVERHEAD_MB,
    _extract_param_billions,
    _find_model,
    _fp16_weights_mb,
    _lookup_gpu_vram,
)


def _kv_per_1k_mb(model_b: float) -> int:
    """KV-cache MB per 1,000 tokens, per sequence.

    `fit` uses a deliberately rough binary heuristic (~2 MB/1k for an 8B), which
    is fine for a fits/doesn't-fit verdict but ~60x too low to derive a *ceiling*
    from. A capacity planner must not overstate how much you can run, so this uses
    a realistic estimate calibrated to a Llama-class fp16 8B (32 layers, 8 KV
    heads, 128 head-dim, GQA → ~0.125 MB/token ≈ 128 MB/1k): `16 × params(B)`.
    It is intentionally conservative (linear in params ignores GQA's KV-head
    sharing, so it slightly *over*-estimates KV for very large models) — erring
    toward under-promising context/concurrency rather than triggering an OOM.
    """
    return max(8, int(16 * model_b))


@dataclass
class QuantCapacity:
    quant: str
    weights_mb: int
    kv_budget_mb: int
    max_context: int        # at --concurrent sequences (capped at model's arch max)
    max_concurrent: int     # at --context tokens
    loads: bool             # do the weights + overhead even fit?
    context_capped: bool    # was max_context limited by the model's arch max?


@dataclass
class CapacityResult:
    model: str
    gpu: str
    vram_mb_available: int
    usable_mb: int          # vram × safety margin
    arch_max_context: int   # the model's trained context window (hard ceiling)
    at_concurrent: int
    at_context: int
    recommended: str        # highest-quality quant that loads with usable context
    rows: list[dict[str, Any]]
    notes: list[str]


# A quant is only "recommended" if it leaves room for a genuinely usable context
# at the requested concurrency, not a few hundred tokens.
_MIN_USABLE_CONTEXT = 4096


def _pick_recommended(rows: list["QuantCapacity"], arch_max: int) -> str:
    """First (= highest-quality, QUANT_CONFIGS is quality-ordered) quant that
    loads with a usable context. Returns "" if nothing qualifies."""
    threshold = min(_MIN_USABLE_CONTEXT, arch_max) if arch_max > 0 else _MIN_USABLE_CONTEXT
    for r in rows:
        if r.loads and r.max_context >= threshold:
            return r.quant
    return ""


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser(
        "capacity",
        help="How far can you push your GPU? (max context / max concurrency).",
    )
    p.add_argument("model", help="Model name (e.g. llama3.1:8b)")
    p.add_argument("--gpu", default="auto",
                   help="GPU name (e.g. 'RTX 4090', 'H100') or 'auto' to detect.")
    p.add_argument("--quant", default="all",
                   help="Quantization to focus on, or 'all' (default).")
    p.add_argument("--context", type=positive_int, default=8192,
                   help="Context length to compute max concurrency at (default: 8192).")
    p.add_argument("--concurrent", type=positive_int, default=1,
                   help="Concurrency to compute max context at (default: 1).")
    p.add_argument("--json", action="store_true", help="JSON output.")
    p.set_defaults(func=run)


def _max_context_tokens(kv_budget_mb: int, kv_per_1k: int, concurrent: int) -> int:
    """Largest context (tokens) whose KV cache fits the budget at N sequences."""
    if kv_budget_mb <= 0:
        return 0
    # budget >= kv_per_1k * (ctx/1000) * concurrent  ->  ctx <= budget*1000/(kv*N)
    return int((kv_budget_mb * 1000) / (kv_per_1k * concurrent))


def _max_concurrent(kv_budget_mb: int, kv_per_1k: int, context: int) -> int:
    """Most sequences whose combined KV cache fits the budget at a context."""
    if kv_budget_mb <= 0:
        return 0
    per_seq_mb = kv_per_1k * (context / 1000)
    if per_seq_mb <= 0:
        return 0
    return int(kv_budget_mb / per_seq_mb)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return an exit code."""
    from aictl.runtime.broker import full_detect
    from aictl.runtime.recommend import MODELS

    if not args.model or not args.model.strip():
        err("Model name is required.")
        print("  Try: aictl capacity llama3.1:8b --gpu 'RTX 4090'")
        return 1

    target = _find_model(args.model, MODELS)
    if target is None:
        err(f"Unknown model: {args.model}")
        print("  Try: aictl recommend  # see available models")
        return 1

    # Resolve GPU + VRAM (same resolution `fit` uses).
    if args.gpu == "auto":
        hw = full_detect()
        if not hw.gpus:
            err("No GPU detected. Pass --gpu explicitly (e.g. --gpu 'RTX 4090').")
            return 1
        gpu_name = hw.gpus[0].name
        vram_mb = hw.gpus[0].vram_mb
    else:
        gpu_name = args.gpu
        vram_mb = _lookup_gpu_vram(gpu_name)
        if vram_mb == 0:
            from aictl.runtime.broker import lookup_apple_silicon_vram
            vram_mb = lookup_apple_silicon_vram(gpu_name)
        if vram_mb == 0:
            err(f"Unknown GPU: {gpu_name}")
            print("  Known GPUs: " + ", ".join(sorted(GPU_VRAM_MB.keys())[:6]) + " ...")
            return 1

    # Case-insensitive quant match against the canonical keys (e.g. "q4_k_m"
    # should resolve to "q4_K_M").
    quant = args.quant.strip()
    if quant.lower() != "all":
        match = next((k for k in QUANT_CONFIGS if k.lower() == quant.lower()), None)
        if match is None:
            err(f"Unknown quant: {args.quant}")
            print("  Choices: all, " + ", ".join(QUANT_CONFIGS.keys()))
            return 1
        quant = match
    else:
        quant = "all"

    model_b = _extract_param_billions(target.name)
    kv_per_1k = _kv_per_1k_mb(model_b)
    usable_mb = int(vram_mb * VRAM_SAFETY)
    # The model's trained context window is a hard ceiling: no amount of VRAM lets
    # you exceed it. Capacity = min(what VRAM allows, what the model supports).
    arch_max = int(getattr(target, "context_length", 0)) or 0

    selected = QUANT_CONFIGS if quant == "all" else {quant: QUANT_CONFIGS[quant]}
    rows: list[QuantCapacity] = []
    for name, (mult, _quality, _notes) in selected.items():
        weights_mb = int(_fp16_weights_mb(model_b) * mult)
        kv_budget = usable_mb - weights_mb - OVERHEAD_MB
        vram_max_ctx = _max_context_tokens(kv_budget, kv_per_1k, args.concurrent)
        capped = arch_max > 0 and vram_max_ctx > arch_max
        eff_max_ctx = min(vram_max_ctx, arch_max) if arch_max > 0 else vram_max_ctx
        rows.append(QuantCapacity(
            quant=name,
            weights_mb=weights_mb,
            kv_budget_mb=max(0, kv_budget),
            max_context=eff_max_ctx,
            max_concurrent=_max_concurrent(kv_budget, kv_per_1k, args.context),
            loads=kv_budget > 0,
            context_capped=capped,
        ))

    notes: list[str] = []
    if not any(r.loads for r in rows):
        margin_pct = round((1 - VRAM_SAFETY) * 100)
        notes.append(
            f"{args.model} weights do not fit {gpu_name} ({vram_mb}MB) at any "
            f"quantization with the {margin_pct}% safety margin. "
            f"Try a smaller model: aictl recommend"
        )

    result = CapacityResult(
        model=args.model, gpu=gpu_name, vram_mb_available=vram_mb,
        usable_mb=usable_mb, arch_max_context=arch_max,
        at_concurrent=args.concurrent, at_context=args.context,
        recommended=_pick_recommended(rows, arch_max),
        rows=[asdict(r) for r in rows], notes=notes,
    )

    if args.json:
        print_json(asdict(result))
        return 0 if any(r.loads for r in rows) else 2

    _display(result, kv_per_1k)
    return 0 if any(r.loads for r in rows) else 2


def _fmt_ctx(tokens: int) -> str:
    """Human context length: 131072 → '128k', 0 → '—'."""
    if tokens <= 0:
        return "—"
    if tokens >= 1000:
        return f"{tokens / 1024:.0f}k" if tokens >= 1024 else f"{tokens}"
    return str(tokens)


def _display(r: CapacityResult, kv_per_1k: int) -> None:
    """Render the capacity table."""
    print()
    print(f"  Model: {r.model}")
    print(f"  GPU:   {r.gpu} ({r.vram_mb_available // 1024}GB, "
          f"{r.usable_mb // 1024}GB usable)")
    print(f"  KV cache: ~{kv_per_1k}MB per 1k tokens / sequence", end="")
    if r.arch_max_context:
        print(f"   ·   model max context: {_fmt_ctx(r.arch_max_context)}")
    else:
        print()
    print()
    print(f"  {'QUANT':<8} {'WEIGHTS':>8}  {'KV BUDGET':>10}  "
          f"{'MAX CTX':>9}  {'MAX CONC':>9}")
    print(f"  {'-' * 8} {'-' * 8}  {'-' * 10}  {'-' * 9}  {'-' * 9}")
    for row in r.rows:
        if not row["loads"]:
            max_ctx, max_conc = "won't load", "—"
        else:
            # Mark contexts pinned to the model's architectural ceiling with '*'.
            max_ctx = _fmt_ctx(row["max_context"]) + ("*" if row["context_capped"] else "")
            max_conc = str(row["max_concurrent"])
        marker = "  ← best" if row["quant"] == r.recommended else ""
        print(f"  {row['quant']:<8} {row['weights_mb'] / 1024:>6.1f}GB  "
              f"{row['kv_budget_mb'] / 1024:>8.1f}GB  {max_ctx:>9}  {max_conc:>9}{marker}")
    print()
    print(f"  MAX CTX  = longest context at {r.at_concurrent} concurrent "
          f"sequence(s)")
    print(f"  MAX CONC = concurrent sequences at {r.at_context}-token context")
    if any(row["loads"] and row["context_capped"] for row in r.rows):
        print(f"  * = pinned to the model's {_fmt_ctx(r.arch_max_context)} "
              f"context limit (VRAM allows more)")
    for n in r.notes:
        print()
        warn(n)
    if any(row["loads"] for row in r.rows):
        print()
        ok(f"Tip: aictl deploy optimize {r.model} --gpu '{r.gpu}'"
           f"  # turn this into vLLM flags")
