"""aictl troubleshoot — symptom-based automatic diagnosis.

When something goes wrong, users don't know if it's the model, the
engine, the driver, or the hardware. This command diagnoses by symptom,
not component. Always emits exactly ONE recommended next step.
"""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, warn, err


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "troubleshoot",
        help="Diagnose a problem you're experiencing.",
    )
    p.add_argument(
        "--symptom",
        choices=["oom", "slow", "wrong-output", "cant-start", "high-cost", "auto"],
        default="auto",
    )
    p.add_argument("--simulate", help="Simulate fitting a model without running it")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return an exit code."""
    if getattr(args, "simulate", None):
        return _diagnose_simulation(args.simulate)

    symptom = args.symptom
    if symptom == "auto":
        symptom = _detect_symptom_from_logs()
        if symptom:
            if not getattr(args, "json", False):
                print(f"\n  Detected symptom: {symptom}\n")
        else:
            if getattr(args, "json", False):
                from aictl.core.output import print_json
                print_json({"symptom": None, "diagnosis": "none", "message": "No obvious problems."})
                return 0
            ok("No obvious problems in recent activity.")
            print("  Run `aictl doctor` for a thorough check.")
            return 0

    diagnosers = {
        "oom": _diagnose_oom,
        "slow": _diagnose_slow,
        "wrong-output": _diagnose_wrong_output,
        "cant-start": _diagnose_cant_start,
        "high-cost": _diagnose_high_cost,
    }
    return diagnosers[symptom]()


def _detect_symptom_from_logs() -> str:
    """Read last ~100 audit events; pick the dominant failure mode."""
    import json
    import os
    from pathlib import Path

    home = os.environ.get("AIOS_STATE_DIR", os.path.expanduser("~/.aios"))
    audit_path = Path(home) / "audit.jsonl"
    if not audit_path.exists():
        return ""

    try:
        with open(audit_path) as f:
            lines = f.readlines()[-100:]
    except OSError:
        return ""

    counters = {"oom": 0, "slow": 0, "cant-start": 0}
    for line in lines:
        try:
            evt = json.loads(line)
        except Exception:
            continue
        msg = json.dumps(evt).lower()
        if "oom" in msg or "out of memory" in msg or "cuda memory" in msg:
            counters["oom"] += 1
        elif "timeout" in msg or "slow" in msg:
            counters["slow"] += 1
        elif "fail" in msg or "error" in msg:
            counters["cant-start"] += 1

    # Threshold-driven: 3+ of one type
    for symptom, count in counters.items():
        if count >= 3:
            return symptom
    return ""


def _diagnose_oom() -> int:
    """Diagnose CUDA or system OOM and print fix instructions."""
    from aictl.runtime.broker import full_detect
    print()
    err("Diagnosis: CUDA out-of-memory")
    print()

    hw = full_detect()
    if not hw.gpus:
        warn("No GPU detected; OOM means system RAM exhausted.")
        print("\n  Fix:")
        print("    aictl ps                # see what's loaded")
        print("    aictl down && aictl recommend  # restart with smaller model")
        return 0

    gpu = hw.gpus[0]
    print(f"  GPU:        {gpu.name}")
    print(f"  Total VRAM: {gpu.vram_mb}MB")
    print()
    print("  Root cause:")
    print("    Model + KV cache + activations exceeded VRAM capacity.")
    print()
    print("  Fix (try in order):")
    print()
    print("    1. Reduce KV cache size:")
    print("         aictl deploy optimize <model> --gpu auto")
    print("       (generates flags with FP8 KV cache, 2× compression)")
    print()
    print("    2. If still OOM, reduce context length:")
    print("         aictl serve <model> --max-model-len 16384")
    print()
    print("    3. If still OOM, switch to smaller quantization:")
    print("         aictl quant recommend <model>")
    print()
    return 0


def _diagnose_slow() -> int:
    """Diagnose slow inference and print optimization steps."""
    from aictl.runtime.broker import full_detect
    from aictl.runtime.adapters import discover_engines

    print()
    warn("Diagnosis: slow inference")
    print()

    hw = full_detect()
    engines = discover_engines()
    engine_map = {e.engine: e for e in engines}
    using_ollama = engine_map.get("ollama") and engine_map["ollama"].reachable
    using_vllm = engine_map.get("vllm") and engine_map["vllm"].reachable

    if using_ollama and not using_vllm and hw.gpus and hw.gpus[0].vram_mb >= 20000:
        print("  Likely cause: Ollama on a GPU big enough for vLLM")
        print()
        print("  Ollama is great for single-user. For >5 concurrent requests,")
        print("  vLLM's continuous batching is ~10x faster.")
        print()
        print("  Fix:")
        print("    aictl deploy optimize <model> --gpu auto")
        print()
        return 0

    print("  Common causes (in order of likelihood):")
    print()
    print("  1. No speculative decoding")
    print("       aictl spec auto <model>     # 2-6× speedup")
    print()
    print("  2. KV cache pressure")
    print("       aictl status                # check memory utilization")
    print()
    print("  3. Prefix caching disabled")
    print("       aictl deploy optimize <model> --gpu auto")
    print()
    return 0


def _diagnose_wrong_output() -> int:
    """Diagnose incorrect model outputs and suggest fixes."""
    print()
    warn("Diagnosis: quality issue")
    print()
    print("  Most common causes (in order):")
    print()
    print("  1. Over-aggressive quantization")
    print("       aictl quant compare <model>")
    print()
    print("  2. Wrong model for the task")
    print("       aictl recommend --use-case <code|chat|reasoning>")
    print()
    print("  3. Prefix cache pollution")
    print("       Set cache_salt per tenant in your requests")
    print()
    print("  4. Sampling parameters")
    print("       Use --temperature 0.0 for deterministic output")
    print()
    return 0


def _diagnose_cant_start() -> int:
    """Diagnose model startup failures and suggest fixes."""
    from aictl.runtime.broker import full_detect
    from aictl.runtime.adapters import discover_engines

    print()
    err("Diagnosis: model won't start")
    print()

    hw = full_detect()
    engines = discover_engines()
    engine_map = {e.engine: e for e in engines}

    checks = [
        ("podman", "Container runtime", lambda: hw.container_runtime != "none"),
        ("ollama", "Ollama reachable",
         lambda: engine_map.get("ollama") and engine_map["ollama"].reachable),
        ("gpu", "GPU detected", lambda: len(hw.gpus) > 0),
        ("nvidia-smi", "NVIDIA driver", _check_nvidia_smi),
    ]

    for name, desc, check in checks:
        try:
            ok_status = bool(check())
        except Exception:
            ok_status = False
        icon = "\u2713" if ok_status else "\u2717"
        print(f"  {icon} {desc}")
        if not ok_status:
            print()
            print("  Fix:")
            if name == "podman":
                print("    sudo dnf install podman    # Fedora/RHEL")
                print("    sudo apt install podman    # Debian/Ubuntu")
            elif name == "ollama":
                print("    curl -fsSL https://ollama.com/install.sh | sh")
            elif name == "nvidia-smi":
                print("    Install NVIDIA driver: https://www.nvidia.com/download/")
            print()
            return 2

    ok("\nAll basic checks passed. Issue is more specific.")
    print()
    print("  Next steps:")
    print("    aictl doctor --deep")
    print("    aictl log --level error -n 50")
    print()
    return 0


def _diagnose_high_cost() -> int:
    """Diagnose unexpectedly high inference costs."""
    print()
    warn("Diagnosis: unexpectedly high cost")
    print()
    print("  Investigate:")
    print("    aictl tco                       # full cost breakdown")
    print("    aictl meter usage --period 30d  # per-team usage")
    print()
    print("  Common causes:")
    print()
    print("  1. Cloud fallback firing too often")
    print("       aictl audit --filter fallback")
    print()
    print("  2. Long contexts (KV cache scales linearly)")
    print("       Implement summarization for >32K contexts")
    print()
    print("  3. Model larger than needed")
    print("       aictl quant recommend <model>")
    print()
    return 0


def _diagnose_simulation(model: str) -> int:
    """Delegate to fit logic without actually serving the model."""
    from aictl.cmd import fit

    fake = argparse.Namespace(
        model=model, gpu="auto", context=8192,
        concurrent=1, use_case="", json=False)
    return fit.run(fake)


def _check_nvidia_smi() -> bool:
    """Validate or inspect the given state."""
    import subprocess
    try:
        return subprocess.run(
            ["nvidia-smi"], capture_output=True, timeout=5
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _parse_size_mb(text: str) -> int:
    """Parse '4.7 GB' or '500 MB' → MB."""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB)", text)
    if not m:
        return 0
    value = float(m.group(1))
    return int(value * 1024) if m.group(2) == "GB" else int(value)
