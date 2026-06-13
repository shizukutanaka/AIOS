"""aictl audit — view audit log."""

from __future__ import annotations

from typing import Any

import argparse
import time

from dataclasses import asdict
from pathlib import Path
from aictl.core.output import print_json, print_table, err
from aictl.core.audit import get_audit_log


def _parse_since(since: str) -> float:
    """Parse relative time string into a Unix timestamp threshold.

    Accepts: Ns, Nm, Nh, Nd  (seconds/minutes/hours/days)
    Returns 0.0 if unparseable (no filter applied).
    """
    if not since:
        return 0.0
    since = since.strip()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if since and since[-1] in units:
        try:
            return time.time() - int(since[:-1]) * units[since[-1]]
        except ValueError:
            pass
    try:
        return float(since)
    except ValueError:
        return 0.0


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("audit", help="View audit log")
    p.add_argument("-n", "--lines", type=int, default=20, help="Number of entries")
    p.add_argument("--event", default="", help="Filter by event type")
    p.add_argument("--since", default="", help="Show entries since duration (e.g. 5m, 1h, 2d)")
    p.add_argument("--resource", default="", help="Filter by resource name (stack/model/node)")
    p.add_argument("--actor", default="", help="Filter by actor (user/system/daemon)")
    p.add_argument("--export", default="", metavar="FILE", help="Export entries to JSON file")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the audit command."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    log = get_audit_log(state_dir)

    # Read a generous pool then apply extra filters client-side
    pool = args.lines * 10 if (getattr(args, "since", "") or getattr(args, "resource", "")
                                or getattr(args, "actor", "")) else args.lines
    entries = log.read(n=pool, event_filter=getattr(args, "event", ""))

    # Apply additional filters
    since_ts = _parse_since(getattr(args, "since", ""))
    resource_filter = getattr(args, "resource", "").lower()
    actor_filter = getattr(args, "actor", "").lower()

    if since_ts > 0:
        entries = [e for e in entries if e.timestamp >= since_ts]
    if resource_filter:
        entries = [e for e in entries if resource_filter in e.resource.lower()]
    if actor_filter:
        entries = [e for e in entries if actor_filter in e.actor.lower()]

    # Trim to requested limit
    entries = entries[:args.lines]

    export_path = getattr(args, "export", "")
    if export_path:
        try:
            Path(export_path).write_text(
                __import__("json").dumps([asdict(e) for e in entries], indent=2)
            )
            from aictl.core.output import ok
            ok(f"Exported {len(entries)} audit entries to {export_path}")
            return 0
        except OSError as e:
            err(f"Cannot write to {export_path}: {e}")
            return 1

    if getattr(args, "json", False):
        print_json([asdict(e) for e in entries])
        return 0

    if not entries:
        print("No audit entries matching the given filters.")
        return 0

    rows = []
    for e in entries:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.timestamp))
        rows.append({"time": ts, "event": e.event, "resource": e.resource,
                     "action": e.action, "outcome": e.outcome, "actor": e.actor})
    print_table(rows, ["time", "event", "resource", "action", "outcome", "actor"])
    return 0
