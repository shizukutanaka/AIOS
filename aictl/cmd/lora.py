"""aictl lora — LoRA adapter management."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json, print_kv, print_table
from aictl.runtime.lora import LoRAManager, LoRAAdapter


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("lora", help="LoRA adapter management")
    lsub = p.add_subparsers(dest="lora_cmd")

    ls = lsub.add_parser("list", help="List registered adapters")
    ls.add_argument("--base", default="", help="Filter by base model")
    ls.set_defaults(func=run_list)

    add = lsub.add_parser("add", help="Register a LoRA adapter")
    add.add_argument("name", help="Adapter name")
    add.add_argument("--base", required=True, help="Base model name")
    add.add_argument("--path", default="", help="Path to adapter weights")
    add.add_argument("--rank", type=int, default=16, help="LoRA rank")
    add.set_defaults(func=run_add)

    budget = lsub.add_parser("budget", help="Show VRAM budget for base model")
    budget.add_argument("base", help="Base model name")
    budget.set_defaults(func=run_budget)

    vllm = lsub.add_parser("vllm-args", help="Generate vLLM LoRA arguments")
    vllm.add_argument("base", help="Base model name")
    vllm.set_defaults(func=run_vllm_args)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_list(args: argparse.Namespace) -> int:
    """Execute the list subcommand."""
    mgr = LoRAManager()
    adapters = mgr.list_adapters(base_model=getattr(args, "base", ""))

    if getattr(args, "json", False):
        print_json([{"name": a.name, "base": a.base_model, "rank": a.rank,
                     "vram_overhead_mb": a.vram_overhead_mb, "active": a.active}
                    for a in adapters])
        return 0

    if not adapters:
        print("No adapters registered. Use: aictl lora add <name> --base <model>")
        return 0

    rows = [{"name": a.name, "base": a.base_model, "rank": a.rank,
             "vram": f"{a.vram_overhead_mb} MB", "active": "\u2713" if a.active else ""}
            for a in adapters]
    print_table(rows, ["name", "base", "rank", "vram", "active"])
    return 0


def run_add(args: argparse.Namespace) -> int:
    """Execute the add subcommand."""
    mgr = LoRAManager()
    adapter = LoRAAdapter(name=args.name, base_model=args.base,
                          path=getattr(args, "path", ""), rank=args.rank)
    mgr.register_adapter(adapter)
    ok(f"Registered adapter: {args.name} (base: {args.base}, rank: {args.rank})")
    return 0


def run_budget(args: argparse.Namespace) -> int:
    """Execute the budget subcommand."""
    mgr = LoRAManager()
    budget = mgr.vram_budget(args.base)

    if getattr(args, "json", False):
        print_json(budget)
        return 0

    ok(f"VRAM Budget: {args.base}")
    print_kv([
        ("Base VRAM", f"{budget['base_vram_mb']} MB"),
        ("Adapter VRAM", f"{budget['adapter_vram_mb']} MB"),
        ("Total VRAM", f"{budget['total_vram_mb']} MB"),
        ("Active adapters", f"{budget['active_adapters']} / {budget['max_adapters']}"),
    ], indent=2)
    return 0


def run_vllm_args(args: argparse.Namespace) -> int:
    """Execute the vllm_args subcommand."""
    mgr = LoRAManager()
    vllm_args = mgr.generate_vllm_args(args.base)
    if vllm_args:
        print(" ".join(vllm_args))
    else:
        print("No active adapters for this base model")
    return 0
