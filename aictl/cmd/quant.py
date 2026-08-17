"""aictl quant — 'which quantization should I use?'

The top question after fit. Users face GGUF Q4_K_M vs AWQ vs GPTQ vs FP8
with no guidance. This makes the decision for them based on GPU, use case,
and engine availability.

April 2026 quality retention benchmarks:
  FP16:    100% (baseline)
  FP8:      99% (Hopper/Blackwell, ~zero loss)
  NVFP4:    97% (Blackwell 4-bit float; microscaling beats INT4)
  AWQ 4b:   95% (best INT4 GPU on Ampere/Ada)
  GGUF Q4:  92% (best portability, CPU-friendly)
  GPTQ 4b:  90% (simpler than AWQ)
  GGUF Q3:  88% (aggressive, code suffers)
"""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, warn, err, print_json


# Empirical quality data, April 2026 benchmarks
QUANT_DATA: dict[str, dict[str, Any]] = {
    "fp16":   {"q_chat": 1.00, "q_code": 1.00, "q_reasoning": 1.00,
               "size": 1.00, "engines": ["vllm", "sglang"], "cc": 0,
               "speed": 1.00, "reasoning_risk": 0.00,
               "notes": "Full precision; only when VRAM abundant."},
    "fp8":    {"q_chat": 0.99, "q_code": 0.99, "q_reasoning": 0.98,
               "size": 0.50, "engines": ["vllm", "sglang"], "cc": 89,
               "speed": 1.30, "reasoning_risk": 0.03,
               "notes": "Best on H100/H200/B200/RTX 5090; near-lossless (W8A8-FP)."},
    "nvfp4":  {"q_chat": 0.97, "q_code": 0.96, "q_reasoning": 0.95,
               "size": 0.27, "engines": ["vllm", "sglang"], "cc": 100,
               "speed": 2.80, "reasoning_risk": 0.12,
               "notes": "4-bit float (NVFP4/MXFP4) on Blackwell; AWQ-class size, "
                        "better accuracy via microscaling. Export: llm-compressor/AutoRound."},
    "awq":    {"q_chat": 0.96, "q_code": 0.95, "q_reasoning": 0.94,
               "size": 0.28, "engines": ["vllm"], "cc": 75,
               "speed": 2.50, "reasoning_risk": 0.32,
               "notes": "Best INT4 chat quality (Ampere/Ada); reasoning loss up to 32% "
                        "(arXiv:2501.03035). AutoAWQ is deprecated — export via "
                        "llm-compressor/GPTQModel."},
    "q4_k_m": {"q_chat": 0.93, "q_code": 0.92, "q_reasoning": 0.91,
               "size": 0.30, "engines": ["ollama", "llama.cpp"], "cc": 0,
               "speed": 1.80, "reasoning_risk": 0.25,
               "notes": "Most portable; works on CPU too."},
    "gptq":   {"q_chat": 0.91, "q_code": 0.90, "q_reasoning": 0.89,
               "size": 0.28, "engines": ["vllm"], "cc": 75,
               "speed": 2.30, "reasoning_risk": 0.32,
               "notes": "Simpler than AWQ; reasoning loss up to 32% on small models."},
    "q3_k_m": {"q_chat": 0.90, "q_code": 0.85, "q_reasoning": 0.84,
               "size": 0.24, "engines": ["ollama", "llama.cpp"], "cc": 0,
               "speed": 1.80, "reasoning_risk": 0.40,
               "notes": "Aggressive; only when Q4 doesn't fit. Avoid for reasoning."},
}

# Worst-case reasoning accuracy degradation (math/logic) per arXiv:2501.03035:
# AWQ/GPTQ show up to 32.39% drop (avg 11.31%) on Llama-3 MATH. The single
# q_reasoning score is an average; reasoning_risk is the worst-case tail that
# matters for math/code-heavy or agentic workloads. Surfaced as a warning.
_REASONING_RISK_THRESHOLD = 0.20


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "quant",
        help="Recommend the best quantization format.",
    )
    sp = p.add_subparsers(dest="quant_cmd")
    sp.required = True

    rec = sp.add_parser("recommend", help="Get one specific recommendation")
    rec.add_argument("model")
    rec.add_argument("--gpu", default="auto")
    rec.add_argument("--use-case", default="chat",
                     choices=["chat", "code", "reasoning", "embedding"])
    rec.set_defaults(func=run_recommend)

    cmp = sp.add_parser("compare", help="Compare all quantization options")
    cmp.add_argument("model")
    cmp.add_argument("--gpu", default="auto")
    cmp.set_defaults(func=run_compare)


def _detect_gpu(args: argparse.Namespace) -> tuple[str, int, int]:
    """Return (gpu_name, vram_mb, compute_capability)."""
    from aictl.cmd.fit import _lookup_gpu_vram
    from aictl.runtime.broker import full_detect

    if args.gpu == "auto":
        hw = full_detect()
        if hw.gpus:
            gpu_name = hw.gpus[0].name
            vram_mb = hw.gpus[0].vram_mb
        else:
            gpu_name = "CPU"
            vram_mb = hw.system.ram_total_mb
    else:
        gpu_name = args.gpu
        vram_mb = _lookup_gpu_vram(gpu_name)

    # Compute capability
    try:
        from aictl.runtime.optimize import GPU_CC
        cc = GPU_CC.get(gpu_name, 0)
    except Exception:
        cc = 0
    return gpu_name, vram_mb, cc


def _q4_k_m_sweet_spot_note(scores: list[dict[str, Any]],
                            best: dict[str, Any]) -> str | None:
    """Return a call-out note if Q4_K_M fits but isn't the top pick.

    Q4_K_M (92% quality, 75% smaller than FP16, runs on CPU too) is the
    community's most widely-adopted format (IMPROVEMENTS.md item H) even
    when the scorer favors something else for this specific GPU/use-case —
    e.g. NVFP4 on a Blackwell card. Surfacing it avoids a user missing the
    most portable option just because a narrower-fit format scored higher.
    """
    if best["quant"] == "q4_k_m":
        return None
    if not any(s["quant"] == "q4_k_m" for s in scores):
        return None
    return ("Q4_K_M remains the community sweet spot for portability "
            "(92% quality, 75% smaller than FP16, runs on CPU too) if you "
            "need broader compatibility than " + best["quant"].upper() + ".")


# FP4 formats whose quality should be validated before trusting in production.
_FP4_QUANTS = ("nvfp4", "mxfp4")


def _fp8_near_lossless_note(scores: list[dict[str, Any]],
                            best: dict[str, Any]) -> str | None:
    """Call out FP8 when an FP4 format wins but FP8 also fits.

    Deployment guidance for Blackwell is consistently "FP8 first, FP4 only if
    you need maximum throughput and can validate quality" — FP8 is
    near-lossless with mature framework support, while FP4 trades a couple of
    quality points for roughly twice the speed. The scorer legitimately
    prefers FP4 on throughput, but a user who cares more about fidelity than
    tokens/sec should not have to read the comparison table to discover that
    a 99% option was sitting right there.

    Mirrors _q4_k_m_sweet_spot_note: surface the runner-up, do not override
    the ranking.
    """
    if best["quant"] not in _FP4_QUANTS:
        return None
    fp8 = next((s for s in scores if s["quant"] == "fp8"), None)
    if fp8 is None:
        return None
    return (f"FP8 also fits at {fp8['quality']*100:.0f}% quality "
            f"({fp8['size_mb']/1024:.1f}GB, {fp8['speed']:.1f}x) and is "
            "near-lossless with more mature framework support. "
            f"{best['quant'].upper()} buys speed for a few quality points — "
            "validate it on your own workload before trusting it in "
            "production.")


def run_recommend(args: argparse.Namespace) -> int:
    """Generate a recommendation."""
    from aictl.cmd.fit import _find_model
    from aictl.runtime.recommend import MODELS

    model = _find_model(args.model, MODELS)
    if model is None:
        err(f"Unknown model: {args.model}")
        return 1

    gpu_name, vram_mb, cc = _detect_gpu(args)

    quality_key = f"q_{args.use_case}"
    scores = []
    for qname, qdata in QUANT_DATA.items():
        if qdata["cc"] > cc:
            continue
        size_mb = int(model.vram_required_mb * qdata["size"]) + 500
        if size_mb > vram_mb * 0.85:
            continue
        quality = qdata.get(quality_key, qdata["q_chat"])
        size_margin = (vram_mb - size_mb) / vram_mb
        score = quality * 0.60 + qdata["speed"] / 3 * 0.25 + size_margin * 0.15
        scores.append({
            "quant": qname, "score": score, "quality": quality,
            "size_mb": size_mb, "speed": qdata["speed"],
            "engine": qdata["engines"][0], "notes": qdata["notes"],
        })

    if not scores:
        err(f"No quantization fits {gpu_name} (VRAM: {vram_mb}MB)")
        print("  Try: aictl recommend  # see models that fit")
        return 2

    scores.sort(key=lambda x: -x["score"])
    best = scores[0]
    sweet_spot = _q4_k_m_sweet_spot_note(scores, best)
    fp8_note = _fp8_near_lossless_note(scores, best)

    if getattr(args, "json", False):
        print_json({"recommended": best, "alternatives": scores[1:],
                    "sweet_spot_note": sweet_spot,
                    # Always present so --json consumers get a stable shape;
                    # null means "not applicable", never "not evaluated".
                    "fp8_note": fp8_note})
        return 0

    print()
    ok(f"Recommended: {best['quant'].upper()} via {best['engine']}")
    print()
    print(f"  Model:    {args.model}")
    print(f"  GPU:      {gpu_name}")
    print(f"  Use case: {args.use_case}")
    print()
    print(f"  Size:     {best['size_mb']/1024:.1f}GB")
    print(f"  Quality:  {best['quality']*100:.0f}% of FP16")
    print(f"  Speed:    {best['speed']:.1f}x vs FP16")
    print(f"  Reason:   {best['notes']}")
    print()

    if len(scores) > 1:
        print("  Other options (ranked):")
        for alt in scores[1:4]:
            print(f"    {alt['quant']:<8}  {alt['quality']*100:.0f}%  "
                  f"{alt['size_mb']/1024:.1f}GB  via {alt['engine']}")
        print()

    # Community sweet-spot call-out: Q4_K_M (92% quality / 75% smaller,
    # portable to CPU) is the most widely-adopted format even when the
    # scorer picks something else for this specific GPU/use-case.
    if sweet_spot:
        print(f"  Note: {sweet_spot}")
        print()

    # FP8-first call-out when an FP4 format wins: FP8 is near-lossless with
    # more mature support, and FP4 quality is workload-dependent enough to
    # warrant validating rather than assuming.
    if fp8_note:
        print(f"  Note: {fp8_note}")
        print()

    # Reasoning-degradation warning (arXiv:2501.03035): aggressive quant can
    # lose up to 32% accuracy on math/logic even when chat quality looks fine.
    if args.use_case == "reasoning":
        risk = QUANT_DATA[best["quant"]].get("reasoning_risk", 0.0)
        if risk >= _REASONING_RISK_THRESHOLD:
            warn(f"Reasoning caution: {best['quant'].upper()} can lose up to "
                 f"{risk*100:.0f}% accuracy on math/logic tasks (arXiv:2501.03035).")
            print("  For math/agentic workloads, prefer fp8 or fp16, or validate "
                  "on your own eval set with: aictl eval")
            print()

    return 0


def run_compare(args: argparse.Namespace) -> int:
    """Compare options side by side."""
    from aictl.cmd.fit import _find_model
    from aictl.runtime.recommend import MODELS

    model = _find_model(args.model, MODELS)
    if model is None:
        err(f"Unknown model: {args.model}")
        return 1

    gpu_name, vram_mb, cc = _detect_gpu(args)

    print()
    print(f"  {args.model} on {gpu_name} ({vram_mb/1024:.0f}GB, CC {cc})")
    print()
    print(f"  {'QUANT':<10} {'SIZE':<8} {'QUALITY':<8} {'SPEED':<7} {'FITS':<6} ENGINE")
    print(f"  {'-'*10} {'-'*8} {'-'*8} {'-'*7} {'-'*6} ------")

    for qname, qdata in QUANT_DATA.items():
        size_mb = int(model.vram_required_mb * qdata["size"]) + 500
        if qdata["cc"] > cc:
            fits = "—"
        elif size_mb <= vram_mb * 0.85:
            fits = "\u2713"
        else:
            fits = "\u2717"
        engines_str = ", ".join(qdata["engines"])
        print(f"  {qname:<10} {size_mb/1024:>4.1f}GB  "
              f"{qdata['q_chat']*100:>3.0f}%      "
              f"{qdata['speed']:.1f}x     "
              f"{fits:<6} {engines_str}")
    print()
    print("  Legend: \u2713 fits | \u2717 too large | — needs newer GPU")
    print()
    return 0
