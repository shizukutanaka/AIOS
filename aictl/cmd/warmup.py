"""aictl warmup — preload frequently used models."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json, print_table
from aictl.core.state import StateStore
from aictl.runtime.warmup import WarmupManager


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("warmup", help="Preload frequently used models")
    wsub = p.add_subparsers(dest="warmup_cmd")

    run_p = wsub.add_parser("run", help="Execute warmup")
    run_p.add_argument("--top", type=int, default=3, help="Number of models to warm up")
    run_p.set_defaults(func=run_warmup)

    stats = wsub.add_parser("stats", help="Show model usage statistics")
    stats.set_defaults(func=run_stats)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_warmup(args: argparse.Namespace) -> int:
    """Execute the warmup subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = WarmupManager(store)
    candidates = mgr.get_warmup_candidates(top_n=getattr(args, "top", 3))

    if not candidates:
        print("No model usage history. Use models first, then run warmup.")
        return 0

    if getattr(args, "json", False):
        print_json([{"model": c.model, "engine": c.engine, "count": c.count} for c in candidates])
        return 0

    ok(f"Warming up {len(candidates)} models...")
    results = mgr.warmup(candidates)
    for r in results:
        icon = "\u2713" if r.get("status") == "loaded" else "\u2717"
        lt = f" ({r['load_time_ms']:.0f}ms)" if "load_time_ms" in r else ""
        print(f"  {icon} {r['model']} [{r['engine']}] — {r['status']}{lt}")
    return 0


def run_stats(args: argparse.Namespace) -> int:
    """Execute the stats subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = WarmupManager(store)
    candidates = mgr.get_warmup_candidates(top_n=20)

    if getattr(args, "json", False):
        print_json([{"model": c.model, "engine": c.engine, "count": c.count,
                     "avg_load_ms": c.avg_load_time_ms} for c in candidates])
        return 0

    if not candidates:
        print("No model usage history yet.")
        return 0

    rows = [{"model": c.model, "engine": c.engine, "uses": c.count,
             "avg_load": f"{c.avg_load_time_ms:.0f}ms"} for c in candidates]
    print_table(rows, ["model", "engine", "uses", "avg_load"])
    return 0
