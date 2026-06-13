"""aictl snapshot — manage context snapshots for safe upgrades."""

from __future__ import annotations

from typing import Any

import argparse


from aictl.core.output import ok, err, print_json, print_table
from aictl.core.state import StateStore
from aictl.core.snapshots import SnapshotManager


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("snapshot", help="Context snapshot management")
    ssub = p.add_subparsers(dest="snap_cmd")

    create = ssub.add_parser("create", help="Create a snapshot")
    create.add_argument("--label", default="", help="Snapshot label")
    create.set_defaults(func=run_create)

    ls = ssub.add_parser("list", help="List snapshots")
    ls.set_defaults(func=run_list)

    restore = ssub.add_parser("restore", help="Restore from a snapshot")
    restore.add_argument("id", help="Snapshot ID or prefix")
    restore.set_defaults(func=run_restore)

    delete = ssub.add_parser("delete", help="Delete a snapshot")
    delete.add_argument("id", help="Snapshot ID or prefix")
    delete.set_defaults(func=run_delete)

    diff = ssub.add_parser("diff", help="Compare two snapshots")
    diff.add_argument("id_a", help="First snapshot ID")
    diff.add_argument("id_b", help="Second snapshot ID")
    diff.set_defaults(func=run_diff)

    export_p = ssub.add_parser("export", help="Export snapshot to a portable JSON file")
    export_p.add_argument("id", help="Snapshot ID or prefix")
    export_p.add_argument("--output", "-o", default="", help="Output file (default: <id>.json)")
    export_p.set_defaults(func=run_export)

    import_p = ssub.add_parser("import", help="Import snapshot from an exported file")
    import_p.add_argument("file", help="Snapshot JSON file to import")
    import_p.add_argument("--restore", action="store_true",
                          help="Also restore state from the imported snapshot")
    import_p.set_defaults(func=run_import)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_create(args: argparse.Namespace) -> int:
    """Execute the create subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = SnapshotManager(store)
    snap = mgr.create(label=getattr(args, "label", ""))

    if getattr(args, "json", False):
        print_json({"id": snap.snapshot_id, "stacks": len(snap.stacks),
                     "models": len(snap.models)})
        return 0

    ok(f"Snapshot created: {snap.snapshot_id}")
    print(f"  Stacks: {len(snap.stacks)}, Models: {len(snap.models)}")
    return 0


def run_list(args: argparse.Namespace) -> int:
    """Execute the list subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = SnapshotManager(store)
    snaps = mgr.list_snapshots()

    if getattr(args, "json", False):
        print_json(snaps)
        return 0

    if not snaps:
        print("No snapshots. Create one: aictl snapshot create")
        return 0

    from aictl.runtime.cache import format_bytes
    rows = []
    for s in snaps:
        rows.append({
            "id": s["id"][:30],
            "version": s["version"],
            "stacks": s["stacks"],
            "models": s["models"],
            "size": format_bytes(s["size_bytes"]),
        })
    print_table(rows, ["id", "version", "stacks", "models", "size"])
    return 0


def run_restore(args: argparse.Namespace) -> int:
    """Execute the restore subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = SnapshotManager(store)
    success, msg = mgr.restore(args.id)

    if getattr(args, "json", False):
        print_json({"success": success, "message": msg})
        return 0 if success else 1

    if success:
        ok(msg)
    else:
        err(msg)
    return 0 if success else 1


def run_delete(args: argparse.Namespace) -> int:
    """Execute the delete subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = SnapshotManager(store)
    if mgr.delete(args.id):
        ok(f"Snapshot deleted: {args.id}")
        return 0
    err(f"Snapshot not found: {args.id}")
    return 1


def run_diff(args: argparse.Namespace) -> int:
    """Compare two snapshots."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = SnapshotManager(store)

    snap_a = mgr._find_snapshot(args.id_a)
    snap_b = mgr._find_snapshot(args.id_b)

    if not snap_a:
        err(f"Snapshot not found: {args.id_a}")
        return 1
    if not snap_b:
        err(f"Snapshot not found: {args.id_b}")
        return 1

    import json
    a = json.loads(snap_a.read_text())
    b = json.loads(snap_b.read_text())

    if getattr(args, "json", False):
        print_json({"a": args.id_a, "b": args.id_b, "diff": _compute_diff(a, b)})
        return 0

    print(f"Diff: {args.id_a[:20]} vs {args.id_b[:20]}")
    print()

    diffs = _compute_diff(a, b)
    if not diffs:
        ok("No differences")
    else:
        for d in diffs:
            print(f"  {d}")

    return 0


def _compute_diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Compute and return the result."""
    diffs = []
    if a.get("version") != b.get("version"):
        diffs.append(f"Version: {a.get('version')} \u2192 {b.get('version')}")
    a_stacks = {s.get("name") for s in a.get("stacks", []) if s.get("name")}
    b_stacks = {s.get("name") for s in b.get("stacks", []) if s.get("name")}
    for s in a_stacks - b_stacks:
        diffs.append(f"Stack removed: {s}")
    for s in b_stacks - a_stacks:
        diffs.append(f"Stack added: {s}")
    a_models = len(a.get("models", []))
    b_models = len(b.get("models", []))
    if a_models != b_models:
        diffs.append(f"Models: {a_models} \u2192 {b_models}")
    return diffs


def run_export(args: argparse.Namespace) -> int:
    """Export a snapshot to a portable JSON file."""
    import json as _json
    store = StateStore(getattr(args, "state_dir", None))
    mgr = SnapshotManager(store)
    snap_path = mgr._find_snapshot(args.id)
    if not snap_path:
        err(f"Snapshot not found: {args.id}")
        return 1

    try:
        data = _json.loads(snap_path.read_text())
    except (OSError, _json.JSONDecodeError) as e:
        err(f"Cannot read snapshot: {e}")
        return 1

    snap_id = data.get("snapshot_id", snap_path.stem)
    out_path = getattr(args, "output", "") or f"{snap_id}.json"

    try:
        from pathlib import Path
        Path(out_path).write_text(_json.dumps(data, indent=2))
    except OSError as e:
        err(f"Cannot write to {out_path}: {e}")
        return 1

    if getattr(args, "json", False):
        print_json({"exported": True, "snapshot_id": snap_id, "file": out_path})
        return 0

    ok(f"Snapshot exported to {out_path}")
    print(f"  stacks : {len(data.get('stacks', []))}")
    print(f"  models : {len(data.get('models', []))}")
    return 0


def run_import(args: argparse.Namespace) -> int:
    """Import a snapshot from an exported JSON file."""
    import json as _json
    from pathlib import Path

    file_path = Path(args.file)
    if not file_path.exists():
        err(f"File not found: {args.file}")
        return 1

    try:
        data = _json.loads(file_path.read_text())
    except (OSError, _json.JSONDecodeError) as e:
        err(f"Cannot parse {args.file}: {e}")
        return 1

    snap_id = data.get("snapshot_id")
    if not snap_id:
        err("File does not look like a snapshot export (missing snapshot_id)")
        return 1

    store = StateStore(getattr(args, "state_dir", None))
    mgr = SnapshotManager(store)
    dest = mgr.snap_dir / f"{snap_id}.json"

    try:
        dest.write_text(_json.dumps(data, indent=2))
    except OSError as e:
        err(f"Cannot write snapshot: {e}")
        return 1

    if getattr(args, "restore", False):
        success, msg = mgr.restore(snap_id)
        if not success:
            err(f"Import succeeded but restore failed: {msg}")
            if getattr(args, "json", False):
                print_json({"imported": True, "snapshot_id": snap_id, "restored": False,
                             "restore_error": msg})
            return 1

    if getattr(args, "json", False):
        print_json({"imported": True, "snapshot_id": snap_id,
                     "restored": getattr(args, "restore", False)})
        return 0

    ok(f"Snapshot {snap_id!r} imported")
    if getattr(args, "restore", False):
        ok("State restored from snapshot")
    return 0
