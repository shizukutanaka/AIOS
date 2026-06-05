"""aictl down — stop and remove a stack's services."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, print_json
from aictl.core.state import StateStore
from aictl.stack.orchestrator import stop_stack


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("down", help="Stop a stack")
    p.add_argument("name", help="Stack name to stop")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the down command."""
    store = StateStore(getattr(args, "state_dir", None))
    stopped = stop_stack(args.name)
    store.remove_stack(args.name)

    if getattr(args, "json", False):
        print_json({"stopped": stopped})
        return 0

    if stopped:
        ok(f"Stack '{args.name}' stopped ({len(stopped)} services)")
    else:
        err(f"No running services found for stack '{args.name}'")

    return 0
