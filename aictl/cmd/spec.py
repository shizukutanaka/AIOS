"""aictl spec — Speculative decoding advisor.

Speculative decoding is a 2026 production standard.
2-3x speedup at zero quality cost.

Research: same model family pairs achieve 80-90% acceptance rate → 2-3x speedup.
Implementation: built into vLLM v0.20, SGLang v0.5.

Usage:
  aictl spec recommend llama3.1:70b   # best draft model (classic pairing)
  aictl spec recommend --all           # full pairing table
  aictl spec methods <model>           # EAGLE-3 / MTP / NGRAM method advisor
  aictl spec bench llama3.1:70b --draft llama3.2:1b
  aictl spec auto <model>              # legacy compat
"""

from __future__ import annotations

from typing import Any

import argparse


class _Pair:
    def __init__(self, target: str, draft: str, runtime: str, acc: float,
                 gamma: int, dp: float, tp: float, notes: str) -> None:
        """Initialize the instance with provided arguments."""
        self.target = target
        self.draft = draft
        self.runtime = runtime
        self.acceptance_rate = acc
        self.gamma = gamma
        self.draft_params_b = dp
        self.target_params_b = tp
        self.notes = notes

    def speedup(self) -> float:
        """Calculate and return the expected speedup ratio."""
        dr = self.draft_params_b / max(self.target_params_b, 1)
        tokens = self.gamma * self.acceptance_rate
        overhead = 1 + dr * self.gamma
        return min(tokens / overhead + 1.0, 3.0)

    def vllm_flags(self) -> str:
        """Generate vLLM serve flags for speculative decoding."""
        return (
            f"vllm serve {self.target} \\\n"
            f"    --speculative-model {self.draft} \\\n"
            f"    --num-speculative-tokens {self.gamma} \\\n"
            f"    --speculative-draft-tensor-parallel-size 1"
        )


PAIRS = [
    _Pair("llama3.1:70b",  "llama3.2:1b",  "vllm", 0.82, 5, 1.0,  70.0, "Same family, same tokenizer. Best pairing."),
    _Pair("llama3.1:70b",  "llama3.2:3b",  "vllm", 0.85, 5, 3.0,  70.0, "Higher acceptance, slightly slower draft."),
    _Pair("llama3.1:8b",   "llama3.2:1b",  "vllm", 0.80, 4, 1.0,  8.0,  "Good for 8B target."),
    _Pair("meta-llama/Llama-3.1-70B-Instruct", "meta-llama/Llama-3.2-1B-Instruct", "vllm", 0.83, 5, 1.0, 70.0, "vLLM native."),
    _Pair("qwen3:32b",     "qwen3:7b",     "vllm", 0.78, 5, 7.0,  32.0, "Same Qwen3 family."),
    _Pair("qwen2.5:14b",   "qwen2.5:3b",   "vllm", 0.79, 5, 3.0,  14.0, "Qwen 2.5 family."),
    _Pair("qwen2.5:72b",   "qwen2.5:7b",   "vllm", 0.81, 5, 7.0,  72.0, "72B → 7B."),
    _Pair("deepseek-r1:32b","deepseek-r1:7b","vllm",0.77, 5, 7.0, 32.0, "R1 reasoning family."),
    _Pair("deepseek-r1:70b","deepseek-r1:7b","vllm",0.76, 5, 7.0, 70.0, "Large R1 → small R1."),
    _Pair("gemma4:27b",    "gemma4:9b",    "vllm", 0.80, 5, 9.0,  27.0, "Gemma 4 family."),
    _Pair("phi4:14b",      "phi4-mini:3.8b","vllm",0.81, 5, 3.8,  14.0, "Microsoft Phi family."),
]


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser("spec", help="Speculative decoding: 2-3x faster inference, zero quality loss.")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sp = p.add_subparsers(dest="spec_cmd", required=False)

    r = sp.add_parser("recommend", help="Best draft model for a target model.")
    r.add_argument("model", nargs="?", default=None)
    r.add_argument("--all", action="store_true", help="Show full table.")
    r.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    r.set_defaults(func=run_recommend)

    m = sp.add_parser("methods", help="EAGLE-3 / P-EAGLE / MTP / NGRAM method advisor.")
    m.add_argument("model", nargs="?", default=None)
    m.add_argument("--all", action="store_true", help="Show the full method matrix.")
    m.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    m.set_defaults(func=run_methods)

    b = sp.add_parser("bench", help="Estimate speedup for a pair.")
    b.add_argument("target")
    b.add_argument("--draft", required=True)
    b.add_argument("--gamma", type=int, default=5)
    b.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    b.set_defaults(func=run_bench)

    # Legacy compat
    for name in ("auto", "vllm", "sglang", "drafts"):
        lp = sp.add_parser(name, help="(legacy) Use 'spec recommend' instead.")
        if name in ("auto", "vllm", "sglang"):
            lp.add_argument("model", nargs="?", default=None)
        lp.set_defaults(func=_legacy_redirect)

    p.set_defaults(func=run_default)


def _legacy_redirect(args: argparse.Namespace) -> int:
    """Redirect legacy subcommands."""
    model = getattr(args, "model", None)
    if model:
        fa = argparse.Namespace(
            model=model, json=getattr(args, "json", False), all=False)
        return run_recommend(fa)
    return run_default(args)


def run_default(args: argparse.Namespace) -> int:
    """Show help."""
    print()
    print("  aictl spec — Speculative Decoding Advisor")
    print()
    print("  2-3x faster inference. Zero quality change.")
    print("  Production standard in vLLM v0.20 + SGLang v0.5 (2026).")
    print()
    print("    aictl spec recommend llama3.1:70b   # best draft model")
    print("    aictl spec recommend --all           # full pairing table")
    print("    aictl spec methods <model>           # EAGLE-3 / MTP / NGRAM advisor")
    print("    aictl spec bench llama3.1:70b --draft llama3.2:1b")
    print()
    return 0


def run_recommend(args: argparse.Namespace) -> int:
    """Recommend draft model(s)."""
    model = getattr(args, "model", None)
    show_all = getattr(args, "all", False)
    use_json = getattr(args, "json", False)

    if show_all:
        if use_json:
            from aictl.core.output import print_json
            print_json([{"target": p.target, "draft": p.draft, "speedup": round(p.speedup(), 2),
                         "acceptance_rate": p.acceptance_rate} for p in sorted(PAIRS, key=lambda p: p.speedup(), reverse=True)])
            return 0
        print()
        print(f"  {'TARGET MODEL':<38} {'DRAFT MODEL':<28} {'SPEEDUP':>8}  {'ACCEPT':>7}")
        print(f"  {'-'*38}  {'-'*28}  {'-'*8}  {'-'*7}")
        for p in sorted(PAIRS, key=lambda p: p.speedup(), reverse=True):
            print(f"  {p.target:<38} {p.draft:<28} {p.speedup():>7.1f}x  {p.acceptance_rate*100:>6.0f}%")
        print(f"\n  {len(PAIRS)} pairs  |  aictl spec recommend <model> for details\n")
        return 0

    if not model:
        return run_default(args)

    pairs = [p for p in PAIRS if p.target.lower() == model.lower()]
    if not pairs:
        pairs = [p for p in PAIRS if model.split(":")[0].lower() in p.target.lower()]

    if not pairs:
        from aictl.core.output import warn
        warn(f"No known draft model for: {model}")
        print(f"\n  Tip: Pick a model from the same family, 10-50x smaller.\n"
              f"  Known targets: {', '.join(sorted(set(p.target for p in PAIRS))[:5])}...\n")
        return 1

    if use_json:
        from aictl.core.output import print_json
        print_json([{"target": p.target, "draft": p.draft, "speedup": round(p.speedup(), 2),
                     "acceptance_rate": p.acceptance_rate, "vllm_flags": p.vllm_flags(), "notes": p.notes}
                    for p in pairs])
        return 0

    best = max(pairs, key=lambda p: p.speedup())
    print()
    print(f"  Speculative decoding for: {model}")
    print()
    if len(pairs) > 1:
        print(f"  {'DRAFT MODEL':<32} {'SPEEDUP':>8}  {'ACCEPT':>7}  NOTES")
        print(f"  {'-'*32}  {'-'*8}  {'-'*7}  -----")
        for p in sorted(pairs, key=lambda p: p.speedup(), reverse=True):
            marker = "  ← recommended" if p is best else ""
            print(f"  {p.draft:<32} {p.speedup():>7.1f}x  {p.acceptance_rate*100:>6.0f}%  {p.notes[:40]}{marker}")
        print()
    print(f"  Best draft: {best.draft}")
    print(f"  Expected speedup: ~{best.speedup():.1f}x  (acceptance ~{best.acceptance_rate*100:.0f}%)")
    print()
    print("  vLLM command:")
    for line in best.vllm_flags().splitlines():
        print(f"    {line}")
    print()
    print(f"  Notes: {best.notes}")
    print(f"\n  ↑ classic draft pairing. For EAGLE-3 (often faster): "
          f"aictl spec methods {model}")
    print("  Source: arxiv.org/abs/2402.01528 · arxiv.org/abs/2503.01840 (EAGLE-3)\n")
    return 0


# ── Modern method advisor (EAGLE-3 / P-EAGLE / MTP / NGRAM) ─────────
# Classic draft-pairing (PAIRS, above) is one method; the 2026 frontier is
# EAGLE-3 (de-facto standard, up to ~4.8x on large models). This advisor
# surfaces the method dimension, backed by runtime/speculative.py so the CLI
# and the actual arg-generation never drift.

# method → (engines, requirement, when_to_use)
_METHOD_INFO: list[tuple[str, str, str, str]] = [
    ("eagle3",     "vLLM + SGLang", "trained EAGLE-3 head (~277MB)",
     "Best general choice when a head exists for the model family."),
    ("p-eagle",    "vLLM",          "EAGLE-3 head + parallel drafting",
     "Extra latency win on supported models (e.g. GPT-OSS)."),
    ("mtp",        "SGLang",        "model-native MTP weights",
     "DeepSeek-V3/R1 and Qwen3 — no separate draft model."),
    ("ngram",      "vLLM + SGLang", "none (GPU n-gram matching)",
     "Any model, zero setup; modest gain on repetitive output."),
    ("standalone", "SGLang",        "any smaller same-family model",
     "No EAGLE head available but you have a small draft."),
]


def run_methods(args: argparse.Namespace) -> int:
    """Advise on the speculative *method* (EAGLE-3/MTP/NGRAM), with engine flags."""
    from aictl.runtime.speculative import (
        auto_select_method, generate_vllm_args, generate_sglang_args,
        estimate_speedup, EAGLE3_DRAFTS, MTP_MODELS, SpeculativeConfig,
    )
    model = getattr(args, "model", None)
    show_all = getattr(args, "all", False)
    use_json = getattr(args, "json", False)

    def _est(method: str) -> dict[str, Any]:
        cfg = SpeculativeConfig(method=method, parallel_drafting=(method == "p-eagle"))
        return estimate_speedup(cfg)

    if show_all or not model:
        matrix = []
        for method, engines, requirement, when in _METHOD_INFO:
            est = _est(method)
            matrix.append({
                "method": method, "engines": engines, "requirement": requirement,
                "latency_speedup": est["estimated_latency_speedup"],
                "throughput_speedup": est["estimated_throughput_speedup"],
                "when_to_use": when,
            })
        if use_json:
            from aictl.core.output import print_json
            print_json(matrix)
            return 0
        print()
        print(f"  {'METHOD':<11} {'ENGINES':<15} {'LAT':>5} {'THRPUT':>7}  REQUIREMENT")
        print(f"  {'-'*11} {'-'*15} {'-'*5} {'-'*7}  {'-'*30}")
        for r in matrix:
            print(f"  {r['method']:<11} {r['engines']:<15} "
                  f"{r['latency_speedup']:>4.1f}x {r['throughput_speedup']:>6.1f}x  {r['requirement']}")
        print()
        print("  EAGLE-3 is the 2026 de-facto standard (vLLM + SGLang).")
        print("  aictl spec methods <model>   # auto-select + ready-to-paste flags\n")
        return 0

    cfg = auto_select_method(model)
    est = estimate_speedup(cfg)
    vllm = generate_vllm_args(cfg)
    sglang = generate_sglang_args(cfg)
    native_mtp = model in MTP_MODELS or any(
        m in model for m in ["DeepSeek-R1", "DeepSeek-V3", "Qwen3"])
    eagle_draft = EAGLE3_DRAFTS.get(model)

    if use_json:
        from aictl.core.output import print_json
        print_json({
            "model": model,
            "method": est["method"],
            "draft_model": cfg.draft_model or None,
            "estimated_latency_speedup": est["estimated_latency_speedup"],
            "estimated_throughput_speedup": est["estimated_throughput_speedup"],
            "vllm_args": vllm,
            "sglang_args": sglang,
            "note": est["note"],
        })
        return 0

    print()
    print(f"  Speculative method for: {model}")
    print()
    print(f"  Selected: {est['method'].upper()}   ({est['note']})")
    if eagle_draft:
        print(f"  EAGLE-3 draft head: {eagle_draft}")
    elif native_mtp:
        print("  Native multi-token-prediction weights (no separate draft).")
    else:
        print("  No EAGLE-3 head known → NGRAM fallback (zero setup).")
    print(f"  Expected: ~{est['estimated_latency_speedup']:.1f}x latency / "
          f"~{est['estimated_throughput_speedup']:.1f}x throughput")
    print()
    if vllm:
        print("  vLLM:")
        for a in vllm:
            print(f"    vllm serve {model} \\\n      {a}")
    if sglang:
        print("  SGLang:")
        print(f"    python -m sglang.launch_server --model {model} \\")
        print("      " + " ".join(sglang))
    print()
    print("  Tip: 'aictl spec methods --all' for the full matrix; "
          "'aictl spec recommend' for classic draft pairing.")
    print("  Source: arxiv.org/abs/2503.01840 (EAGLE-3)\n")
    return 0


def run_bench(args: argparse.Namespace) -> int:
    """Estimate speedup for a custom pair."""
    target_name = args.target
    draft_name = args.draft
    gamma = args.gamma
    use_json = getattr(args, "json", False)

    def _pb(name: Any) -> float:
        """Return the parameter count in billions parsed from a model name."""
        for s, b in [("1b",1),("3b",3),("7b",7),("8b",8),("9b",9),("14b",14),
                     ("27b",27),("32b",32),("70b",70),("72b",72)]:
            if s in name.lower():
                return float(b)
        return 7.0

    existing = next((p for p in PAIRS if p.target == target_name and p.draft == draft_name), None)
    if existing:
        acc = existing.acceptance_rate
    else:
        same_fam = target_name.split(":")[0].lower() == draft_name.split(":")[0].lower()
        acc = 0.80 if same_fam else 0.70

    tp = _pb(target_name)
    dp = _pb(draft_name)
    pair = _Pair(target_name, draft_name, "vllm", acc, gamma, dp, tp, "Custom pair estimate")
    s = pair.speedup()

    if use_json:
        from aictl.core.output import print_json
        print_json({"target": target_name, "draft": draft_name, "gamma": gamma,
                    "acceptance_rate": acc, "estimated_speedup": round(s, 2), "vllm_flags": pair.vllm_flags()})
        return 0

    dr = dp / tp
    print()
    print(f"  Target:  {target_name}  ({tp:.0f}B)")
    print(f"  Draft:   {draft_name}   ({dp:.0f}B)")
    print(f"  γ = {gamma}  acceptance ≈ {acc*100:.0f}%")
    print()
    print(f"  Estimated speedup: ~{s:.1f}x")
    print(f"  Math: {gamma}×{acc:.0%} ÷ (1+{dr:.2f}×{gamma}) + 1 = {s:.2f}x")
    print()
    print("  vLLM command:")
    for line in pair.vllm_flags().splitlines():
        print(f"    {line}")
    print()
    return 0
