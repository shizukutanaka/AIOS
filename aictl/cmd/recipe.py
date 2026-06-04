"""aictl recipe — list and run built-in AI recipes."""

from __future__ import annotations

from typing import Any

import argparse

import time

from aictl.core.output import ok, err, print_json
from aictl.core.state import StateStore, StackEntry
from aictl.stack.manifest import list_recipes, get_recipe
from aictl.stack.orchestrator import apply_stack


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("recipe", help="Built-in AI recipes")
    rsub = p.add_subparsers(dest="recipe_cmd")

    ls = rsub.add_parser("list", help="List available recipes")
    ls.set_defaults(func=run_list)

    run_p = rsub.add_parser("run", help="Run a recipe")
    run_p.add_argument("name", help="Recipe name")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.set_defaults(func=run_recipe)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_list(args: argparse.Namespace) -> int:
    """Execute the list subcommand."""
    names = list_recipes()
    if getattr(args, "json", False):
        print_json(names)
        return 0

    print("Available recipes:")
    for name in names:
        m = get_recipe(name)
        svc_count = len(m.services) if m else 0
        gpu = any(s.gpu_required for s in (m.services if m else []))
        tag = " [GPU]" if gpu else ""
        print(f"  {name} — {svc_count} services{tag}")

    print("\nRun with: aictl recipe run <name>")
    return 0


def run_recipe(args: argparse.Namespace) -> int:
    """Execute the recipe subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    manifest = get_recipe(args.name)

    if manifest is None:
        err(f"Unknown recipe: {args.name}")
        print(f"Available: {', '.join(list_recipes())}")
        return 1

    dry = getattr(args, "dry_run", False)
    results = apply_stack(manifest, dry_run=dry)

    entry = StackEntry(
        name=manifest.name,
        file=manifest.source_file,
        applied_at=time.time(),
        status="running" if not dry else "dry-run",
        services=[{"name": r.name, "status": r.status, "endpoint": r.endpoint} for r in results],
    )
    if not dry:
        store.upsert_stack(entry)

    if getattr(args, "json", False):
        print_json({"recipe": args.name, "services": [r.__dict__ for r in results]})
        return 0

    label = "[DRY RUN] " if dry else ""
    ok(f"{label}Recipe '{args.name}' started")
    for r in results:
        icon = "✓" if r.status in ("running", "starting", "dry-run") else "✗"
        ep = f" → {r.endpoint}" if r.endpoint else ""
        print(f"  {icon} {r.name} [{r.status}]{ep}")

    return 0
