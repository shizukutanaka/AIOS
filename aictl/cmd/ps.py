"""aictl ps — list running AI services."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import print_json, print_table
from aictl.core.state import StateStore
from aictl.stack.orchestrator import list_running


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("ps", help="List running AI services")
    p.add_argument("--stack", default="", help="Filter by stack name")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the ps command."""
    services = list_running(getattr(args, "stack", ""))
    stacks = StateStore(getattr(args, "state_dir", None)).load_stacks()

    if getattr(args, "json", False):
        print_json({"services": services, "stacks": [s.__dict__ for s in stacks]})
        return 0

    if services:
        print_table(services, ["name", "status", "ports", "container_id"])
    elif stacks:
        print("No running containers. Applied stacks:")
        for s in stacks:
            print(f"  {s.name} — {s.status} (applied {s.file})")
    else:
        print("No services running. Try: aictl recipe run local-chat")

    return 0
