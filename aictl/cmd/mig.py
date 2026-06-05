"""aictl mig — MIG partition planning and management."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, print_json, print_table
from aictl.runtime.broker import full_detect
from aictl.runtime.mig import plan_partitions, generate_mig_commands, ModelRequirement


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("mig", help="MIG partition planning")
    msub = p.add_subparsers(dest="mig_cmd")

    plan = msub.add_parser("plan", help="Plan MIG partitions for multi-model serving")
    plan.add_argument("--models", nargs="+", help="model:vram_gb pairs (e.g. llama3:16 qwen:8)")
    plan.set_defaults(func=run_plan)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_plan(args: argparse.Namespace) -> int:
    """Execute the plan subcommand."""
    report = full_detect()
    mig_gpus = [g for g in report.gpus if g.mig_capable]

    if not mig_gpus:
        err("No MIG-capable GPUs detected (requires A100/H100/H200)")
        return 1

    # Parse model requirements
    models = []
    for spec in (getattr(args, "models", None) or ["llama3:16", "embedding:2"]):
        parts = spec.split(":")
        name = parts[0]
        vram = int(parts[1]) if len(parts) > 1 else 16
        models.append(ModelRequirement(name=name, vram_gb=vram))

    if getattr(args, "json", False):
        plans = []
        for gpu in mig_gpus:
            plan = plan_partitions(gpu.name, gpu.index, models)
            plans.append(plan.__dict__)
        print_json(plans)
        return 0

    for gpu in mig_gpus:
        plan = plan_partitions(gpu.name, gpu.index, models)
        ok(f"GPU {gpu.index}: {gpu.name} ({plan.total_vram_gb}GB)")

        rows = [{"profile": p["profile"], "model": p["model"],
                 "vram": p.get("vram_gb", "?") + "GB",
                 "slices": p.get("slices", "?")} for p in plan.partitions]
        print_table(rows, ["profile", "model", "vram", "slices"])

        print(f"  Utilization: {plan.utilization:.0%} | Waste: {plan.waste_gb}GB")
        print()

        cmds = generate_mig_commands(plan)
        if cmds:
            print("  Commands to apply:")
            for cmd in cmds:
                print(f"    $ {cmd}")
            print()

    return 0
