"""aictl lora — LoRA adapter management."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, print_json, print_kv, print_table
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

    inspect = lsub.add_parser("inspect", help="Show full details for an adapter")
    inspect.add_argument("name", help="Adapter name")
    inspect.set_defaults(func=run_inspect)

    delete = lsub.add_parser("delete", help="Remove a registered adapter")
    delete.add_argument("name", help="Adapter name")
    delete.set_defaults(func=run_delete)

    activate = lsub.add_parser("activate", help="Mark adapter as active")
    activate.add_argument("name", help="Adapter name")
    activate.set_defaults(func=run_activate)

    deactivate = lsub.add_parser("deactivate", help="Mark adapter as inactive")
    deactivate.add_argument("name", help="Adapter name")
    deactivate.set_defaults(func=run_deactivate)

    route = lsub.add_parser("route", help="Set traffic weight for an adapter")
    route.add_argument("name", help="Adapter name")
    route.add_argument("--weight", type=int, default=100,
                       help="Traffic weight 0-100 (proportional routing)")
    route.set_defaults(func=run_route)

    autotune = lsub.add_parser("auto-tune", help="Recommend which adapters to keep loaded")
    autotune.add_argument("base", help="Base model name")
    autotune.add_argument("--vram", type=int, default=24,
                          help="Available VRAM in GB for adapter budget")
    autotune.set_defaults(func=run_autotune)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_list(args: argparse.Namespace) -> int:
    """Execute the list subcommand."""
    mgr = LoRAManager()
    # Strip the base filter for symmetry: `lora add` stores a stripped base
    # (Pass 115), so a padded filter must still match (cf. Pass 122).
    adapters = mgr.list_adapters(base_model=_norm_name(getattr(args, "base", "")))

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


def _norm_name(raw: str) -> str:
    """Normalize an adapter or base-model name. Leading/trailing whitespace is
    never part of an adapter's identity; an empty/whitespace-only name is invalid
    (callers reject it)."""
    return (raw or "").strip()


def run_add(args: argparse.Namespace) -> int:
    """Execute the add subcommand."""
    name = _norm_name(args.name)
    if not name:
        err("Adapter name is required (empty or whitespace-only is not allowed).")
        return 1
    base = _norm_name(getattr(args, "base", ""))
    if not base:
        err("Base model name is required (empty or whitespace-only is not allowed).")
        return 1
    mgr = LoRAManager()
    adapter = LoRAAdapter(name=name, base_model=base,
                          path=getattr(args, "path", ""), rank=args.rank)
    mgr.register_adapter(adapter)
    ok(f"Registered adapter: {name} (base: {base}, rank: {args.rank})")
    return 0


def run_budget(args: argparse.Namespace) -> int:
    """Execute the budget subcommand."""
    base = _norm_name(args.base)
    mgr = LoRAManager()
    budget = mgr.vram_budget(base)

    if getattr(args, "json", False):
        print_json(budget)
        return 0

    ok(f"VRAM Budget: {base}")
    print_kv([
        ("Base VRAM", f"{budget['base_vram_mb']} MB"),
        ("Adapter VRAM", f"{budget['adapter_vram_mb']} MB"),
        ("Total VRAM", f"{budget['total_vram_mb']} MB"),
        ("Active adapters", f"{budget['active_adapters']} / {budget['max_adapters']}"),
    ], indent=2)
    return 0


def run_vllm_args(args: argparse.Namespace) -> int:
    """Execute the vllm_args subcommand."""
    # Strip the base for lookup symmetry: `lora add` stores a stripped base
    # (Pass 115), so a padded query here must still match.
    base = _norm_name(args.base)
    mgr = LoRAManager()
    vllm_args = mgr.generate_vllm_args(base)

    # The universal --json contract (CLAUDE.md) applies to every command: emit
    # machine-readable output, never the human "No active adapters" sentence.
    if getattr(args, "json", False):
        print_json({"base": base, "args": vllm_args})
        return 0

    if vllm_args:
        print(" ".join(vllm_args))
    else:
        print("No active adapters for this base model")
    return 0


def run_inspect(args: argparse.Namespace) -> int:
    """Show full metadata for a single adapter."""
    name = _norm_name(args.name)
    mgr = LoRAManager()
    adapters = mgr.list_adapters()
    match = next((a for a in adapters if a.name == name), None)

    if match is None:
        err(f"Adapter not found: {name}")
        return 1

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json(asdict(match))
        return 0

    ok(f"Adapter: {match.name}")
    print_kv([
        ("base_model",  match.base_model),
        ("path",        match.path or "(none)"),
        ("rank",        str(match.rank)),
        ("vram_mb",     str(match.vram_overhead_mb)),
        ("active",      str(match.active)),
        ("weight",      str(match.traffic_weight)),
    ], indent=2)
    return 0


def run_delete(args: argparse.Namespace) -> int:
    """Remove an adapter from the registry."""
    name = _norm_name(args.name)
    mgr = LoRAManager()
    data = mgr._load()
    if name not in data.get("adapters", {}):
        err(f"Adapter not found: {name}")
        return 1
    del data["adapters"][name]
    mgr._save(data)
    ok(f"Adapter deleted: {name}")
    return 0


def run_activate(args: argparse.Namespace) -> int:
    """Mark an adapter as active."""
    name = _norm_name(args.name)
    mgr = LoRAManager()
    data = mgr._load()
    if name not in data.get("adapters", {}):
        err(f"Adapter not found: {name}")
        return 1
    data["adapters"][name]["active"] = True
    mgr._save(data)
    ok(f"Adapter activated: {name}")
    return 0


def run_deactivate(args: argparse.Namespace) -> int:
    """Mark an adapter as inactive."""
    name = _norm_name(args.name)
    mgr = LoRAManager()
    data = mgr._load()
    if name not in data.get("adapters", {}):
        err(f"Adapter not found: {name}")
        return 1
    data["adapters"][name]["active"] = False
    mgr._save(data)
    ok(f"Adapter deactivated: {name}")
    return 0


def run_route(args: argparse.Namespace) -> int:
    """Set traffic weight for a LoRA adapter (proportional routing)."""
    name = _norm_name(args.name)
    weight = max(0, min(100, getattr(args, "weight", 100)))
    mgr = LoRAManager()
    data = mgr._load()
    if name not in data.get("adapters", {}):
        err(f"Adapter not found: {name}")
        return 1
    data["adapters"][name]["traffic_weight"] = weight
    mgr._save(data)

    if getattr(args, "json", False):
        print_json({"name": name, "traffic_weight": weight})
        return 0

    ok(f"Adapter {name} → weight {weight}")
    # Show sibling weights for the same base model
    adapter_data = data["adapters"][name]
    base = adapter_data.get("base_model", "")
    siblings = [(n, d["traffic_weight"])
                for n, d in data["adapters"].items()
                if d.get("base_model") == base]
    if len(siblings) > 1:
        total = sum(w for _, w in siblings)
        print()
        for name, w in sorted(siblings, key=lambda x: -x[1]):
            pct = w / max(total, 1) * 100
            print(f"  {name:<30} {w:>3}  ({pct:.0f}%)")
    return 0


def run_autotune(args: argparse.Namespace) -> int:
    """Recommend which adapters to keep loaded given the VRAM budget."""
    # A VRAM budget is a physical quantity: a value < 1 GB is meaningless and
    # (worse) a negative budget makes `used + overhead <= budget` always false,
    # silently evicting every adapter while printing a nonsensical negative
    # "X MB / -Y MB" line. Reject it up front (cf. `fit` --context/--concurrent,
    # `cost forecast` --gpus).
    vram_gb = getattr(args, "vram", 24)
    if vram_gb < 1:
        err(f"--vram must be >= 1 GB (got {vram_gb}).")
        return 1
    base = _norm_name(args.base)  # match the stripped storage key (Pass 115/122)
    mgr = LoRAManager()
    adapters = mgr.list_adapters(base_model=base)
    vram_budget_mb = vram_gb * 1024

    if not adapters:
        print(f"No adapters registered for base model: {base}")
        return 0

    # Sort by traffic_weight desc — keep highest-traffic adapters in VRAM
    sorted_adapters = sorted(adapters, key=lambda a: a.traffic_weight, reverse=True)
    used_mb = 0
    keep: list = []
    evict: list = []

    for a in sorted_adapters:
        if used_mb + a.vram_overhead_mb <= vram_budget_mb:
            keep.append(a)
            used_mb += a.vram_overhead_mb
        else:
            evict.append(a)

    if getattr(args, "json", False):
        print_json({
            "base_model": base,
            "vram_budget_mb": vram_budget_mb,
            "vram_used_mb": used_mb,
            "keep": [a.name for a in keep],
            "evict": [a.name for a in evict],
        })
        return 0

    ok(f"LoRA auto-tune for {base} ({getattr(args, 'vram', 24)} GB VRAM budget)")
    print(f"\n  Used: {used_mb} MB / {vram_budget_mb} MB")
    if keep:
        print("\n  Keep loaded (by traffic weight):")
        for a in keep:
            print(f"    ✓ {a.name:<30} weight={a.traffic_weight}  {a.vram_overhead_mb} MB")
    if evict:
        print("\n  Evict (low traffic / over budget):")
        for a in evict:
            print(f"    ✗ {a.name:<30} weight={a.traffic_weight}  {a.vram_overhead_mb} MB")
    return 0
