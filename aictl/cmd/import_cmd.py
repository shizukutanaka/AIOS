"""aictl import — import a bundle from aictl export bundle."""

from __future__ import annotations

from typing import Any

import argparse
import json

from aictl.core.output import ok, err, warn, print_json
from aictl.core.state import StateStore, StackEntry


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("import", help="Import a bundle from aictl export")
    p.add_argument("file", help="Bundle file path (JSON from 'aictl export bundle')")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be imported without writing state")
    p.add_argument("--skip-models", action="store_true",
                   help="Skip model registry entries")
    p.add_argument("--skip-stacks", action="store_true",
                   help="Skip stack entries")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the import command."""
    from pathlib import Path

    path = Path(args.file)
    if not path.exists():
        err(f"File not found: {args.file}")
        if getattr(args, "json", False):
            print_json({"success": False, "error": f"file not found: {args.file}"})
        return 1

    try:
        bundle = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        err(f"Failed to read bundle: {e}")
        if getattr(args, "json", False):
            print_json({"success": False, "error": str(e)})
        return 1

    version = bundle.get("export_version", "1")
    raw_stacks = bundle.get("stacks", [])
    raw_models = bundle.get("models", [])

    dry = getattr(args, "dry_run", False)
    skip_models = getattr(args, "skip_models", False)
    skip_stacks = getattr(args, "skip_stacks", False)

    stacks_imported = 0
    models_imported = 0
    warnings: list[str] = []

    store = StateStore(getattr(args, "state_dir", None))

    if not skip_stacks:
        for d in raw_stacks:
            try:
                entry = StackEntry(**{k: v for k, v in d.items()
                                      if k in StackEntry.__dataclass_fields__})
                if not dry:
                    store.upsert_stack(entry)
                stacks_imported += 1
            except Exception as exc:
                warnings.append(f"stack {d.get('name', '?')}: {exc}")

    if not skip_models:
        for m in raw_models:
            try:
                if not dry:
                    store.register_model(
                        model_id=m.get("id", ""),
                        name=m.get("name", ""),
                        digest=m.get("digest", ""),
                        fmt=m.get("format", "gguf"),
                        signed=bool(m.get("signed", False)),
                    )
                models_imported += 1
            except Exception as exc:
                warnings.append(f"model {m.get('name', '?')}: {exc}")

    if getattr(args, "json", False):
        print_json({
            "success": True,
            "dry_run": dry,
            "stacks_imported": stacks_imported,
            "models_imported": models_imported,
            "warnings": warnings,
            "export_version": version,
        })
        return 0

    label = "[DRY RUN] " if dry else ""
    ok(f"{label}Import complete: {stacks_imported} stack(s), {models_imported} model(s)")
    for w in warnings:
        warn(w)
    return 0
