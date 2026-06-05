"""aictl audit — view audit log."""

from __future__ import annotations

from typing import Any

import argparse

from pathlib import Path
from aictl.core.output import print_json, print_table
from aictl.core.audit import get_audit_log


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("audit", help="View audit log")
    p.add_argument("-n", "--lines", type=int, default=20, help="Number of entries")
    p.add_argument("--event", default="", help="Filter by event type")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the audit command."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    log = get_audit_log(state_dir)
    entries = log.read(n=args.lines, event_filter=getattr(args, "event", ""))

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json([asdict(e) for e in entries])
        return 0

    if not entries:
        print("No audit entries. Events are recorded when you use aictl commands.")
        return 0

    import time
    rows = []
    for e in entries:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.timestamp))
        rows.append({"time": ts, "event": e.event, "resource": e.resource,
                     "action": e.action, "outcome": e.outcome})
    print_table(rows, ["time", "event", "resource", "action", "outcome"])
    return 0
