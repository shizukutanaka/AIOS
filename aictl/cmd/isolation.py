"""aictl isolation — cgroup v2 process isolation for inference workloads."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, print_json, print_kv, print_table
from aictl.runtime.isolation import (
    detect_cpu_isolation_support,
    generate_isolation_for_model,
    generate_systemd_slice,
    IsolationConfig,
)


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("isolation", help="cgroup v2 process isolation for inference")
    isub = p.add_subparsers(dest="isolation_cmd")

    det = isub.add_parser("detect", help="Detect available isolation features")
    det.set_defaults(func=run_detect)

    cfg = isub.add_parser("config", help="Generate isolation config for a model")
    cfg.add_argument("model", help="Model name (e.g. llama3:8b)")
    cfg.add_argument("--params", type=float, default=7.0, help="Parameter count in billions")
    cfg.add_argument("--vram", type=int, default=0, help="Available VRAM in GB (0=CPU mode)")
    cfg.add_argument("--ram", type=int, default=0, help="Available RAM in GB")
    cfg.set_defaults(func=run_config)

    apply = isub.add_parser("apply", help="Generate systemd slice unit for a model")
    apply.add_argument("model", help="Model name")
    apply.add_argument("--params", type=float, default=7.0, help="Parameter count in billions")
    apply.add_argument("--vram", type=int, default=0, help="Available VRAM in GB")
    apply.add_argument("--output", default="", help="Write slice file to path (default: stdout)")
    apply.set_defaults(func=run_apply)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_detect(args: argparse.Namespace) -> int:
    """Show cgroup/CPU isolation features available on this system."""
    features = detect_cpu_isolation_support()

    if getattr(args, "json", False):
        print_json(features)
        return 0

    ok("CPU / cgroup isolation support")
    rows = [{"feature": k, "available": "✓" if v else "✗"} for k, v in features.items()]
    print_table(rows, ["feature", "available"])

    supported = sum(features.values())
    total = len(features)
    print(f"\n  {supported}/{total} features available")
    if not features.get("cgroup_v2"):
        print("  ⚠  cgroup v2 not detected — isolation will be limited")
    return 0


def run_config(args: argparse.Namespace) -> int:
    """Generate an IsolationConfig for the specified model."""
    cfg = generate_isolation_for_model(
        model_name=args.model,
        model_params_b=getattr(args, "params", 7.0),
        vram_gb=getattr(args, "vram", 0),
        ram_gb=getattr(args, "ram", 0),
    )

    if getattr(args, "json", False):
        print_json({
            "name": cfg.name,
            "memory_min_gb": cfg.memory_min_gb,
            "memory_max_gb": cfg.memory_max_gb,
            "memory_high_gb": cfg.memory_high_gb,
            "cpu_cores": cfg.cpu_cores,
            "numa_node": cfg.numa_node,
            "io_weight": cfg.io_weight,
            "oom_score_adj": cfg.oom_score_adj,
            "nice": cfg.nice,
        })
        return 0

    ok(f"Isolation config: {cfg.name}")
    print_kv([
        ("memory_min",  f"{cfg.memory_min_gb} GB"),
        ("memory_max",  f"{cfg.memory_max_gb} GB"),
        ("memory_high", f"{cfg.memory_high_gb} GB"),
        ("io_weight",   str(cfg.io_weight)),
        ("oom_score",   str(cfg.oom_score_adj)),
        ("nice",        str(cfg.nice)),
    ], indent=2)
    return 0


def run_apply(args: argparse.Namespace) -> int:
    """Generate a systemd slice unit and optionally write it to disk."""
    cfg = generate_isolation_for_model(
        model_name=args.model,
        model_params_b=getattr(args, "params", 7.0),
        vram_gb=getattr(args, "vram", 0),
    )
    unit = generate_systemd_slice(cfg)
    output = getattr(args, "output", "")

    if output:
        from pathlib import Path
        try:
            Path(output).write_text(unit)
            ok(f"Slice written: {output}")
            if getattr(args, "json", False):
                print_json({"written": True, "path": output, "name": cfg.name})
        except OSError as exc:
            err(f"Failed to write slice: {exc}")
            return 1
    else:
        print(unit)

    return 0
