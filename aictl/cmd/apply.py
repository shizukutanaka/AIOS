"""aictl apply — apply a Stack manifest to start AI services.

Supports two modes:
  default:  Start services via podman run / ollama (immediate, ephemeral)
  --quadlet: Generate systemd Quadlet units (persistent, survives reboot)
"""

from __future__ import annotations

from typing import Any

import argparse

import time

from aictl.core.output import ok, err, warn, print_json
from aictl.core.state import StateStore, StackEntry
from aictl.stack.manifest import parse_file, StackParseError
from aictl.stack.orchestrator import apply_stack
from aictl.stack.quadlet import generate_quadlets, write_quadlets, reload_systemd


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("apply", help="Apply a Stack manifest")
    asub = p.add_subparsers(dest="apply_cmd")

    rb = asub.add_parser("rollback", help="Re-apply a stack from its last recorded manifest")
    rb.add_argument("name", help="Stack name to roll back")
    rb.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    rb.add_argument("--json", action="store_true", help="JSON output")
    rb.set_defaults(func=run_rollback)

    p.add_argument("-f", "--file", required=False, default="", help="Path to stack manifest")
    p.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    p.add_argument("--validate-only", action="store_true",
                   help="Parse and validate manifest without applying")
    p.add_argument("--quadlet", action="store_true",
                   help="Generate systemd Quadlet units (persistent)")
    p.add_argument("--root", action="store_true",
                   help="Install Quadlet units system-wide (requires root)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=run)


def run_rollback(args: argparse.Namespace) -> int:
    """Re-apply a stack using the manifest file path recorded in state."""
    store = StateStore(getattr(args, "state_dir", None))
    stacks = store.load_stacks()
    match = next((s for s in stacks if s.name == args.name), None)
    if not match:
        err(f"Stack '{args.name}' not found in state")
        if getattr(args, "json", False):
            print_json({"success": False, "error": f"stack '{args.name}' not in state"})
        return 1

    if not match.file:
        err(f"No manifest file recorded for stack '{args.name}'")
        if getattr(args, "json", False):
            print_json({"success": False, "error": "no manifest file recorded"})
        return 1

    ok(f"Rolling back stack '{args.name}' using {match.file}...")
    # Re-use run() with a synthetic args object
    rollback_args = argparse.Namespace(
        file=match.file,
        dry_run=getattr(args, "dry_run", False),
        validate_only=False,
        quadlet=False,
        root=False,
        json=getattr(args, "json", False),
        state_dir=getattr(args, "state_dir", None),
    )
    return run(rollback_args)


def run(args: argparse.Namespace) -> int:
    """Execute the apply command."""
    store = StateStore(getattr(args, "state_dir", None))
    file_path = getattr(args, "file", "")
    if not file_path:
        err("No manifest file specified. Use -f <path>")
        return 1
    try:
        manifest = parse_file(file_path)
    except StackParseError as e:
        err(str(e))
        if getattr(args, "json", False):
            print_json({"valid": False, "error": str(e)})
        return 1

    if getattr(args, "validate_only", False):
        services = [{"name": s.name, "runtime": s.runtime, "model": s.model}
                    for s in manifest.services]
        if getattr(args, "json", False):
            print_json({"valid": True, "stack": manifest.name,
                        "services": len(manifest.services), "service_list": services})
        else:
            ok(f"Manifest valid: '{manifest.name}' ({len(manifest.services)} services)")
            for s in manifest.services:
                print(f"  - {s.name} ({s.runtime or 'no runtime'})")
        return 0

    dry = getattr(args, "dry_run", False)
    quadlet = getattr(args, "quadlet", False)

    if quadlet:
        return _apply_quadlet(args, manifest, store, dry)
    return _apply_direct(args, manifest, store, dry)


def _apply_direct(args: argparse.Namespace, manifest: Any, store: Any, dry: Any) -> int:
    """Apply a stack directly without Quadlet."""
    file_path = getattr(args, "file", "")
    results = apply_stack(manifest, dry_run=dry)
    entry = StackEntry(
        name=manifest.name, file=file_path, applied_at=time.time(),
        status="running" if not dry else "dry-run",
        services=[{"name": r.name, "status": r.status, "endpoint": r.endpoint} for r in results],
    )
    if not dry:
        store.upsert_stack(entry)

    if getattr(args, "json", False):
        print_json({"stack": manifest.name, "mode": "direct",
                     "services": [r.__dict__ for r in results]})
        return 0

    label = "[DRY RUN] " if dry else ""
    ok(f"{label}Stack '{manifest.name}' applied (direct)")
    for r in results:
        icon = "\u2713" if r.status in ("running", "starting", "dry-run") else "\u2717"
        ep = f" \u2192 {r.endpoint}" if r.endpoint else ""
        detail = f" ({r.error})" if r.error else ""
        print(f"  {icon} {r.name} [{r.status}]{ep}{detail}")

    if not dry:
        from aictl.core.hooks import on_stack_applied
        on_stack_applied(manifest.name, file_path, mode="direct",
                         services=len(results), state_dir=store.dir)

    return 0


def _apply_quadlet(args: argparse.Namespace, manifest: Any, store: Any, dry: Any) -> int:
    """Apply a stack using Quadlet units."""
    rootless = not getattr(args, "root", False)
    units = generate_quadlets(manifest, rootless=rootless)
    if not units:
        warn("No container services to generate Quadlet units for")
        return 0

    if getattr(args, "json", False):
        print_json({"stack": manifest.name, "mode": "quadlet", "rootless": rootless,
                     "units": [{"filename": u.filename, "service": u.service_name} for u in units]})
        if dry:
            return 0

    written = write_quadlets(units, rootless=rootless, dry_run=dry)
    entry = StackEntry(
        name=manifest.name, file=getattr(args, "file", ""), applied_at=time.time(),
        status="quadlet-installed" if not dry else "dry-run",
        services=[{"name": u.service_name, "status": "installed"} for u in units],
    )
    if not dry:
        store.upsert_stack(entry)

    label = "[DRY RUN] " if dry else ""
    ok(f"{label}Stack '{manifest.name}' \u2014 {len(units)} Quadlet units")
    for u in units:
        print(f"  \u2713 {u.filename} \u2192 {u.service_name}")

    if dry:
        print("\nPreview:")
        for u in units:
            print(f"\n\u2500\u2500 {u.filename} \u2500\u2500")
            print(u.content)
    else:
        for p in written:
            print(f"  Written: {p}")
        if reload_systemd():
            ok("systemd daemon-reload complete")
        else:
            warn("Reload manually: systemctl --user daemon-reload")
    return 0
