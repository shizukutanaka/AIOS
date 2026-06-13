"""aictl engines — discover and inspect inference engines."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, print_json, print_table
from aictl.runtime.adapters import discover_engines, EngineHealth


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("engines", help="Discover and inspect inference engines")
    esub = p.add_subparsers(dest="engines_cmd")

    ls = esub.add_parser("list", help="List discovered engines and their status")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=run_list)

    health = esub.add_parser("health", help="Show detailed engine health")
    health.add_argument("--engine", default="", help="Filter by engine type (vllm/ollama/sglang)")
    health.add_argument("--json", action="store_true")
    health.set_defaults(func=run_health)

    models = esub.add_parser("models", help="List models loaded across all engines")
    models.add_argument("--json", action="store_true")
    models.set_defaults(func=run_models)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def _get_healths(args: argparse.Namespace) -> list[EngineHealth]:
    from pathlib import Path
    from aictl.core.config import load_config
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    config = load_config(state_dir)
    endpoints = config.engines.to_dict() if config else None
    return discover_engines(endpoints)


def run_list(args: argparse.Namespace) -> int:
    """List all discovered engines with status summary."""
    healths = _get_healths(args)

    rows = [
        {
            "engine": h.engine,
            "endpoint": h.endpoint,
            "status": h.status,
            "reachable": h.reachable,
            "models": len(h.models),
            "latency_ms": round(h.latency_ms, 1),
        }
        for h in healths
    ]

    if getattr(args, "json", False):
        print_json(rows)
        return 0

    if not rows:
        print("No engines discovered.")
        return 0

    print_table(rows, ["engine", "endpoint", "status", "reachable", "models", "latency_ms"])
    reachable = sum(1 for h in healths if h.reachable)
    print(f"\n  {reachable}/{len(healths)} engines reachable")
    return 0


def run_health(args: argparse.Namespace) -> int:
    """Show detailed health info for each engine."""
    healths = _get_healths(args)
    engine_filter = getattr(args, "engine", "")
    if engine_filter:
        healths = [h for h in healths if h.engine == engine_filter]

    if not healths:
        msg = f"No engines found" + (f" for type '{engine_filter}'" if engine_filter else "")
        err(msg)
        return 1

    dicts = [
        {
            "engine": h.engine,
            "endpoint": h.endpoint,
            "reachable": h.reachable,
            "status": h.status,
            "models": h.models,
            "version": h.version,
            "latency_ms": round(h.latency_ms, 1),
            "error": h.error,
        }
        for h in healths
    ]

    if getattr(args, "json", False):
        print_json(dicts)
        return 0

    for d in dicts:
        icon = "✓" if d["reachable"] else "✗"
        print(f"  {icon} {d['engine']}  {d['endpoint']}  [{d['status']}]")
        if d["models"]:
            print(f"      models: {', '.join(d['models'])}")
        if d["version"]:
            print(f"      version: {d['version']}")
        if d["latency_ms"] > 0:
            print(f"      latency: {d['latency_ms']}ms")
        if d["error"]:
            print(f"      error: {d['error']}")

    return 0


def run_models(args: argparse.Namespace) -> int:
    """List all models loaded across discovered engines."""
    healths = _get_healths(args)

    rows = [
        {"engine": h.engine, "endpoint": h.endpoint, "model": m}
        for h in healths
        for m in h.models
    ]

    if getattr(args, "json", False):
        print_json(rows)
        return 0

    if not rows:
        print("No models loaded (or no engines reachable).")
        return 0

    print_table(rows, ["engine", "model", "endpoint"])
    return 0
