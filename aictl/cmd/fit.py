"""aictl fit — answers the most-asked question in local AI: 'will this model fit?'

Ollama can't answer this. vLLM doesn't try. Users discover the answer
only after a failed download or OOM crash. We answer it before the work begins.

Design principle: show the answer, then show what fits instead.
"""

from __future__ import annotations

import argparse

from dataclasses import asdict, dataclass
from typing import Any

from aictl.core.output import ok, warn, err, print_json


# GPU VRAM catalog (April 2026)
GPU_VRAM_MB = {
    "RTX 3060": 12288, "RTX 3070": 8192, "RTX 3080": 10240, "RTX 3090": 24576,
    "RTX 4060": 8192, "RTX 4070": 12288, "RTX 4080": 16384, "RTX 4090": 24576,
    "RTX 5070": 12288, "RTX 5080": 16384, "RTX 5090": 32768,
    "A100 40GB": 40960, "A100 80GB": 81920, "A100": 81920,
    "H100": 81920, "H200": 143360, "B200": 196608, "GB200": 196608,
    "L40S": 49152, "L4": 24576, "T4": 16384,
}


@dataclass
class FitResult:
    model: str
    gpu: str
    vram_mb_available: int
    fits: bool
    quants: dict[str, dict[str, Any]]
    alternatives: list[dict[str, Any]]
    notes: list[str]


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "fit",
        help="Check if a model fits your GPU (before downloading anything).",
    )
    p.add_argument("model", help="Model name (e.g. llama3:70b, qwen3:7b)")
    p.add_argument("--gpu", default="auto", help="GPU type override")
    p.add_argument("--context", type=int, default=8192, help="Context length")
    p.add_argument("--concurrent", type=int, default=1, help="Concurrent requests")
    p.add_argument("--use-case", default="", help="chat | code | embedding")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return an exit code."""
    from aictl.runtime.broker import full_detect
    from aictl.runtime.recommend import MODELS, recommend

    # Input validation — never accept empty or whitespace-only model names
    if not args.model or not args.model.strip():
        err("Model name is required.")
        print("  Try: aictl fit qwen3:7b --gpu auto")
        return 1

    target = _find_model(args.model, MODELS)
    if target is None:
        err(f"Unknown model: {args.model}")
        print("  Try: aictl recommend  # see available models")
        return 1

    unified = False
    if args.gpu == "auto":
        hw = full_detect()
        if not hw.gpus:
            return _analyze_cpu(target, hw)
        gpu_name = hw.gpus[0].name
        vram_mb = hw.gpus[0].vram_mb
        unified = getattr(hw.gpus[0], "unified_memory", False)
    else:
        gpu_name = args.gpu
        vram_mb = _lookup_gpu_vram(gpu_name)
        if vram_mb == 0:
            # Apple Silicon chips have unified memory, not a fixed VRAM catalog.
            from aictl.runtime.broker import lookup_apple_silicon_vram
            vram_mb = lookup_apple_silicon_vram(gpu_name)
            unified = vram_mb > 0
        if vram_mb == 0:
            err(f"Unknown GPU: {gpu_name}")
            print("  Known GPUs: " + ", ".join(sorted(GPU_VRAM_MB.keys())[:5]) + "...")
            return 1

    quants = _calculate_quantizations(target, args.context, args.concurrent)
    fits_any = False
    for name, data in quants.items():
        data["fits"] = data["total_mb"] <= vram_mb * 0.90
        if data["fits"]:
            fits_any = True

    alternatives = []
    if not fits_any:
        recs = recommend(
            vram_mb=vram_mb,
            use_case=args.use_case or target.use_case,
            max_results=5,
        )
        alternatives = [
            {"name": r.name, "vram_mb": r.vram_required_mb,
             "use_case": r.use_case, "notes": r.notes}
            for r in recs
        ]

    notes = []
    if unified:
        notes.append(
            f"{gpu_name} uses unified memory: ~{vram_mb}MB of system RAM is "
            f"addressable as VRAM (budgeted at 75%). Use MLX or Ollama (Metal)."
        )
    if not fits_any:
        notes.append(
            f"{args.model} (FP16) needs {target.vram_required_mb}MB "
            f"but {gpu_name} has only {vram_mb}MB."
        )

    if getattr(args, "json", False):
        print_json(asdict(FitResult(
            model=args.model, gpu=gpu_name, vram_mb_available=vram_mb,
            fits=fits_any, quants=quants, alternatives=alternatives, notes=notes,
        )))
        return 0

    _display(args.model, gpu_name, vram_mb, quants, alternatives, notes)
    return 0 if fits_any else 2


def _find_model(name: str, models: list[Any]) -> Any:
    """Fuzzy match. 'llama3-8b' should find 'llama3.1:8b'."""
    def norm(s: str) -> str:
        """Normalize a value to the range [0, 1]."""
        s = s.lower().replace(":", "-").replace("/", "-").replace(".", "-")
        while "--" in s:
            s = s.replace("--", "-")
        return s.strip("-")

    target = norm(name)
    for m in models:
        if norm(m.name) == target or target in norm(m.name) or norm(m.name) in target:
            return m
    # Token match: all query tokens must appear
    q_tokens = [t for t in target.split("-") if t]
    for m in models:
        mn = norm(m.name)
        if all(t in mn for t in q_tokens):
            return m
    return None


def _lookup_gpu_vram(gpu_name: str) -> int:
    """Return VRAM in MB for a known GPU name, or 0 if unknown."""
    for k, v in GPU_VRAM_MB.items():
        if k.lower() in gpu_name.lower():
            return v
    return 0


def _extract_param_billions(name: str) -> float:
    """Extract parameter count from model name (e.g. "7b" → 7.0)."""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)[bB]", name)
    return float(m.group(1)) if m else 7.0


def _calculate_quantizations(model: Any, context: int, concurrent: int) -> dict[str, Any]:
    """Calculate and return the numeric result."""
    base_mb = model.vram_required_mb
    model_b = _extract_param_billions(model.name)
    kv_per_1k = max(1, int(2 * (model_b / 7.0)))
    kv_total = int(kv_per_1k * (context / 1000) * concurrent)
    overhead = 500  # CUDA context

    configs = {
        "fp16": (1.00, 1.00, "Full precision"),
        "fp8": (0.50, 0.99, "Hopper/Blackwell/Ada (CC≥89)"),
        "q8_0": (0.55, 0.98, "GGUF Q8 (Ollama/llama.cpp)"),
        "awq": (0.28, 0.95, "AWQ 4-bit (vLLM, best quality 4-bit)"),
        "q4_K_M": (0.30, 0.92, "GGUF Q4 (recommended for Ollama)"),
        "q3_K_M": (0.24, 0.88, "GGUF Q3 (aggressive)"),
    }

    return {
        name: {
            "weights_mb": int(base_mb * mult),
            "kv_cache_mb": kv_total,
            "overhead_mb": overhead,
            "total_mb": int(base_mb * mult) + kv_total + overhead,
            "quality": quality,
            "notes": notes_str,
            "fits": False,
        }
        for name, (mult, quality, notes_str) in configs.items()
    }


def _first_fit(quants: dict[str, Any]) -> str:
    """Return the first quantization level that fits in available VRAM."""
    for name in ["fp16", "fp8", "q8_0", "awq", "q4_K_M", "q3_K_M"]:
        if quants.get(name, {}).get("fits"):
            return name
    return ""


def _display(model: str, gpu: str, vram_mb: int, quants: dict[str, Any],
             alternatives: list[Any], notes: list[Any]) -> None:
    """Print the fit analysis table to stdout."""
    print()
    print(f"  Model: {model}")
    print(f"  GPU:   {gpu} ({vram_mb / 1024:.0f}GB)")
    print()
    print(f"  {'QUANT':<10} {'SIZE':<10} {'QUALITY':<10} FITS")
    print(f"  {'-' * 10} {'-' * 10} {'-' * 10} ----")
    best = _first_fit(quants)
    for name, d in quants.items():
        symbol = "\u2713" if d["fits"] else "\u2717"
        marker = " \u2190 best" if name == best else ""
        print(f"  {name:<10} {d['total_mb']/1024:>5.1f}GB     "
              f"{d['quality']*100:>3.0f}%        {symbol}{marker}")
    print()

    for note in notes:
        warn(note)

    if alternatives:
        print()
        print("  Models that fit your GPU:")
        for alt in alternatives[:5]:
            print(f"    {alt['name']:<28} {alt['vram_mb']/1024:>5.1f}GB  "
                  f"{alt['use_case']:<10} {alt['notes']}")
    print()


def _analyze_cpu(model: Any, hw: Any) -> int:
    """Analyze CPU-only inference feasibility."""
    ram_mb = hw.system.ram_total_mb
    warn(f"No GPU detected. Analyzing for CPU (RAM: {ram_mb}MB)")
    print()
    print(f"  Model: {model.name}")
    q4_mb = int(model.vram_required_mb * 0.30) + 500
    if q4_mb <= ram_mb * 0.70:
        ok(f"Q4_K_M should work ({q4_mb}MB needed, {ram_mb*0.7:.0f}MB available)")
        print("  Expected: slow but functional CPU inference")
        return 0
    else:
        err(f"Not enough RAM even at Q4_K_M ({q4_mb}MB needed)")
        return 2
