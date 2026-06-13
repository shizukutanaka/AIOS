"""aictl context — manage inference context continuity."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json, print_table
from aictl.core.config import load_config
from aictl.core.state import StateStore
from aictl.runtime.continuity import ContextContinuityEngine, ContextSnapshot


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

    switch = csub.add_parser("switch", help="Restore a specific context snapshot by ID")
    switch.add_argument("snapshot_id", help="Snapshot ID (or prefix)")
    switch.set_defaults(func=run_switch)

    export_p = csub.add_parser("export", help="Export a snapshot to a portable file")
    export_p.add_argument("snapshot_id", help="Snapshot ID")
    export_p.add_argument("--output", default="", help="Output file path (default: <id>.json)")
    export_p.set_defaults(func=run_export)

    import_p = csub.add_parser("import", help="Import a snapshot from a file")
    import_p.add_argument("file", help="Path to snapshot file")
    import_p.set_defaults(func=run_import)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_save(args: argparse.Namespace) -> int:
    """Execute the save subcommand."""
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
    import time

    engine = ContextContinuityEngine()
    snapshots = engine.list_snapshots()

    if not snapshots:
        print("No saved contexts. Use: aictl context save")
        return 0

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json([asdict(s) for s in snapshots])
        return 0

    rows = [{"id": s.snapshot_id[:16], "engine": s.engine,
             "entries": s.num_entries, "status": s.status,
             "age": _format_age(time.time() - s.created_at)} for s in snapshots]
    print_table(rows, ["id", "engine", "entries", "status", "age"])
    return 0


def run_gc(args: argparse.Namespace) -> int:
    """Execute the gc subcommand."""
    engine = ContextContinuityEngine()
    removed = engine.gc(max_age_hours=getattr(args, "max_age", 24))
    ok(f"Removed {removed} stale context snapshots")
    return 0


def run_switch(args: argparse.Namespace) -> int:
    """Restore a specific snapshot by ID or prefix."""
    from aictl.core.output import err
    import json

    engine = ContextContinuityEngine()
    snapshots = engine.list_snapshots()
    match = next((s for s in snapshots
                  if s.snapshot_id == args.snapshot_id
                  or s.snapshot_id.startswith(args.snapshot_id)), None)

    if match is None:
        err(f"Snapshot not found: {args.snapshot_id}")
        return 1

    store = StateStore(getattr(args, "state_dir", None))
    config = load_config(store.dir)
    engines = config.engines.to_dict()
    endpoint = engines.get(match.engine, "")

    if not endpoint:
        from aictl.core.output import warn
        warn(f"Engine '{match.engine}' not configured — snapshot marked but not applied")
        match.status = "saved"
    else:
        try:
            engine._restore_engine_context(match, endpoint)
            match.status = "restored"
        except Exception as exc:
            from aictl.core.output import warn
            warn(f"Restore attempt failed: {exc}")
            match.status = "failed"

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json({"switched": True, "snapshot_id": match.snapshot_id,
                    "engine": match.engine, "status": match.status})
        return 0

    ok(f"Switched to context: {match.snapshot_id} ({match.status})")
    return 0


def run_export(args: argparse.Namespace) -> int:
    """Export a snapshot to a portable JSON file."""
    from aictl.core.output import err
    import json
    from pathlib import Path

    engine = ContextContinuityEngine()
    snapshots = engine.list_snapshots()
    match = next((s for s in snapshots
                  if s.snapshot_id == args.snapshot_id
                  or s.snapshot_id.startswith(args.snapshot_id)), None)

    if match is None:
        err(f"Snapshot not found: {args.snapshot_id}")
        return 1

    output = getattr(args, "output", "") or f"{match.snapshot_id}.json"
    from dataclasses import asdict
    data = asdict(match)

    try:
        Path(output).write_text(json.dumps(data, indent=2))
    except OSError as exc:
        err(f"Failed to write: {exc}")
        return 1

    if getattr(args, "json", False):
        print_json({"exported": True, "snapshot_id": match.snapshot_id, "output": output})
        return 0

    ok(f"Snapshot exported: {output}")
    return 0


def run_import(args: argparse.Namespace) -> int:
    """Import a context snapshot from a file."""
    from aictl.core.output import err
    import json
    from pathlib import Path

    f = Path(args.file)
    if not f.exists():
        err(f"File not found: {args.file}")
        return 1

    try:
        data = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        err(f"Invalid JSON: {exc}")
        return 1

    # Validate required fields
    if "snapshot_id" not in data or "engine" not in data:
        err("Invalid snapshot file: missing snapshot_id or engine")
        return 1

    engine = ContextContinuityEngine()
    # Add to index
    existing = engine.list_snapshots()
    snap = ContextSnapshot(**{k: data[k] for k in ContextSnapshot.__dataclass_fields__ if k in data})
    by_id = {s.snapshot_id: s for s in existing}
    by_id[snap.snapshot_id] = snap
    engine._save_index(list(by_id.values()))

    if getattr(args, "json", False):
        print_json({"imported": True, "snapshot_id": snap.snapshot_id, "engine": snap.engine})
        return 0

    ok(f"Snapshot imported: {snap.snapshot_id} ({snap.engine})")
    return 0


def _format_age(seconds: float) -> str:
    """Format the value for display or export."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.0f}m"
    return f"{seconds/3600:.1f}h"
