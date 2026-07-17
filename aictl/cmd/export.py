"""aictl export — export stacks, models, and config as a portable bundle."""

from __future__ import annotations

from typing import Any

import argparse
import json
import time
from pathlib import Path

from aictl.core.output import ok, err, warn, print_json
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("export", help="Export stacks, models, and config")
    esub = p.add_subparsers(dest="export_cmd")

    stack_p = esub.add_parser("stack", help="Export a named stack as YAML")
    stack_p.add_argument("name", help="Stack name")
    stack_p.add_argument("-o", "--output", default="", help="Output file path (stdout if omitted)")
    stack_p.add_argument("--json", action="store_true", help="JSON output instead of YAML")
    stack_p.set_defaults(func=run_stack)

    bundle_p = esub.add_parser("bundle", help="Export all stacks + models + config as a JSON bundle")
    bundle_p.add_argument("-o", "--output", default="", help="Output file path (stdout if omitted)")
    bundle_p.add_argument("--pretty", action="store_true", default=True,
                          help="Pretty-print JSON (default: True)")
    bundle_p.set_defaults(func=run_bundle)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_stack(args: argparse.Namespace) -> int:
    """Export a single stack entry as YAML or JSON."""
    store = StateStore(getattr(args, "state_dir", None))
    stacks = store.load_stacks()
    entry = next((s for s in stacks if s.name == args.name), None)
    if not entry:
        err(f"Stack '{args.name}' not found in state")
        return 1

    from dataclasses import asdict
    data = asdict(entry)

    if getattr(args, "json", False):
        text = json.dumps(data, indent=2)
    else:
        text = _stack_to_yaml(data)

    output = getattr(args, "output", "")
    if output:
        Path(output).write_text(text)
        ok(f"Stack '{args.name}' exported to {output}")
    else:
        print(text)
    return 0


def run_bundle(args: argparse.Namespace) -> int:
    """Export a full bundle: all stacks + registered models + node state."""
    from dataclasses import asdict

    store = StateStore(getattr(args, "state_dir", None))
    stacks = store.load_stacks()
    models = store.list_models()
    node = store.load_node()

    bundle = {
        "export_version": "1",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node": asdict(node),
        "stacks": [asdict(s) for s in stacks],
        "models": models,
    }

    indent = 2 if getattr(args, "pretty", True) else None
    text = json.dumps(bundle, indent=indent)

    output = getattr(args, "output", "")
    if output:
        Path(output).write_text(text)
        ok(f"Bundle exported to {output} ({len(stacks)} stacks, {len(models)} models)")
    else:
        print(text)
    return 0


def _stack_to_yaml(data: dict) -> str:
    """Render a StackEntry dict as a minimal YAML string (stdlib, no PyYAML)."""
    lines = [f"name: {data.get('name', '')}"]
    lines.append(f"file: {data.get('file', '')}")
    status = data.get("status", "")
    if status:
        lines.append(f"status: {status}")
    applied = data.get("applied_at", 0.0)
    if applied:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(applied))
        lines.append(f"applied_at: {ts}")
    services = data.get("services", [])
    if services:
        lines.append("services:")
        for svc in services:
            lines.append(f"  - name: {svc.get('name', '')}")
            svc_status = svc.get("status", "")
            if svc_status:
                lines.append(f"    status: {svc_status}")
            ep = svc.get("endpoint", "")
            if ep:
                lines.append(f"    endpoint: {ep}")
    return "\n".join(lines) + "\n"
