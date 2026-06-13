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

    asub = p.add_subparsers(dest="audit_cmd")

    stats = asub.add_parser("stats", help="Summarize audit events by type/actor/outcome")
    stats.add_argument("--since", default="7d", dest="stats_since",
                       help="Time window (e.g. 7d, 24h). default: 7d")
    stats.add_argument("--top", type=int, default=10, help="Top N event types to show")
    stats.set_defaults(func=run_stats)

    purge = asub.add_parser("purge", help="Delete audit log files older than N days")
    purge.add_argument("--max-age", type=int, default=30, dest="max_age",
                       help="Delete audit files older than N days (default: 30)")
    purge.add_argument("--dry-run", action="store_true",
                       help="Show what would be deleted without deleting")
    purge.set_defaults(func=run_purge)


def run_stats(args: argparse.Namespace) -> int:
    """Summarize audit events by type, actor, and outcome."""
    from collections import Counter
    from pathlib import Path as _Path
    state_dir = _Path(args.state_dir) if getattr(args, "state_dir", None) else None
    log = get_audit_log(state_dir)
    since_ts = _parse_since(getattr(args, "stats_since", "7d"))
    entries = log.read(n=100000, event_filter="")
    if since_ts > 0:
        entries = [e for e in entries if e.timestamp >= since_ts]

    total = len(entries)
    by_event: Counter = Counter(e.event for e in entries)
    by_actor: Counter = Counter(e.actor for e in entries)
    by_outcome: Counter = Counter(e.outcome for e in entries)
    top = getattr(args, "top", 10)
    top_events = by_event.most_common(top)

    if getattr(args, "json", False):
        print_json({
            "total": total,
            "window": getattr(args, "stats_since", "7d"),
            "top_events": [{"event": e, "count": c} for e, c in top_events],
            "by_actor": dict(by_actor),
            "by_outcome": dict(by_outcome),
        })
        return 0

    from aictl.core.output import ok as _ok
    _ok(f"Audit Stats (last {getattr(args, 'stats_since', '7d')}) — {total} events")
    print()
    print(f"  Top {min(top, len(top_events))} event types:")
    for evt, cnt in top_events:
        print(f"    {cnt:>5}  {evt}")
    print()
    print("  By actor:")
    for actor, cnt in sorted(by_actor.items(), key=lambda x: -x[1]):
        print(f"    {cnt:>5}  {actor}")
    print()
    print("  By outcome:")
    for outcome, cnt in sorted(by_outcome.items(), key=lambda x: -x[1]):
        print(f"    {cnt:>5}  {outcome}")
    return 0


def run_purge(args: argparse.Namespace) -> int:
    """Delete audit log files older than max_age days."""
    import time as _time
    from pathlib import Path as _Path
    state_dir = _Path(args.state_dir) if getattr(args, "state_dir", None) else None
    log = get_audit_log(state_dir)
    max_age_secs = getattr(args, "max_age", 30) * 86400
    dry_run = getattr(args, "dry_run", False)
    now = _time.time()

    to_delete = [
        p for p in sorted(log.dir.glob("audit-*.jsonl"))
        if (now - p.stat().st_mtime) > max_age_secs
    ]

    if not to_delete:
        print("No audit files match purge criteria.")
        if getattr(args, "json", False):
            print_json({"purged": 0, "dry_run": dry_run})
        return 0

    if getattr(args, "json", False):
        print_json({
            "purged": 0 if dry_run else len(to_delete),
            "dry_run": dry_run,
            "files": [str(p.name) for p in to_delete],
        })
        if not dry_run:
            for p in to_delete:
                p.unlink(missing_ok=True)
        return 0

    from aictl.core.output import ok as _ok
    action = "Would delete" if dry_run else "Deleting"
    _ok(f"{action} {len(to_delete)} audit file(s) (>{args.max_age} days old)")
    for p in to_delete:
        print(f"  - {p.name}")
    if not dry_run:
        for p in to_delete:
            p.unlink(missing_ok=True)
        _ok("Purge complete")
    else:
        print("\n  (dry-run — no changes made)")
    return 0


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
