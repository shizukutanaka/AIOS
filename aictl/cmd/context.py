"""aictl context — manage inference context continuity."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json, print_table
from aictl.core.config import load_config
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("context", help="Inference context continuity")
    csub = p.add_subparsers(dest="context_cmd")

    save = csub.add_parser("save", help="Save engine contexts before upgrade")
    save.set_defaults(func=run_save)

    restore = csub.add_parser("restore", help="Restore contexts after upgrade")
    restore.set_defaults(func=run_restore)

    ls = csub.add_parser("list", help="List saved context snapshots")
    ls.set_defaults(func=run_list)

    gc = csub.add_parser("gc", help="Garbage collect stale snapshots")
    gc.add_argument("--max-age", type=int, default=24, help="Max age in hours")
    gc.set_defaults(func=run_gc)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_save(args: argparse.Namespace) -> int:
    """Execute the save subcommand."""
    from aictl.runtime.continuity import ContextContinuityEngine
    store = StateStore(getattr(args, "state_dir", None))
    config = load_config(store.dir)
    engine = ContextContinuityEngine()

    ok("Saving engine contexts...")
    snapshots = engine.pre_upgrade_save(config.engines.to_dict())

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json([asdict(s) for s in snapshots])
        return 0

    for s in snapshots:
        icon = "\u2713" if s.status == "saved" else "\u2717"
        print(f"  {icon} {s.engine}: {s.num_entries} entries ({s.status})")

    ok(f"{len(snapshots)} contexts saved")
    return 0


def run_restore(args: argparse.Namespace) -> int:
    """Execute the restore subcommand."""
    from aictl.runtime.continuity import ContextContinuityEngine
    store = StateStore(getattr(args, "state_dir", None))
    config = load_config(store.dir)
    engine = ContextContinuityEngine()

    ok("Restoring engine contexts...")
    restored = engine.post_upgrade_restore(config.engines.to_dict())

    for s in restored:
        print(f"  \u2713 {s.engine}: {s.model} restored")

    ok(f"{len(restored)} contexts restored")
    return 0


def run_list(args: argparse.Namespace) -> int:
    """Execute the list subcommand."""
    from aictl.runtime.continuity import ContextContinuityEngine
    import time

    engine = ContextContinuityEngine()
    snapshots = engine.list_snapshots()

    if not snapshots:
        print("No saved contexts. Use: aictl context save")
        return 0

    rows = [{"id": s.snapshot_id[:16], "engine": s.engine,
             "entries": s.num_entries, "status": s.status,
             "age": _format_age(time.time() - s.created_at)} for s in snapshots]
    print_table(rows, ["id", "engine", "entries", "status", "age"])
    return 0


def run_gc(args: argparse.Namespace) -> int:
    """Execute the gc subcommand."""
    from aictl.runtime.continuity import ContextContinuityEngine
    engine = ContextContinuityEngine()
    removed = engine.gc(max_age_hours=getattr(args, "max_age", 24))
    ok(f"Removed {removed} stale context snapshots")
    return 0


def _format_age(seconds: float) -> str:
    """Format the value for display or export."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.0f}m"
    return f"{seconds/3600:.1f}h"
