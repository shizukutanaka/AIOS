"""aictl perf — show recent CLI performance.

Auto-collected from every command invocation. Helps identify regressions
and slow commands without external profiling tools.
"""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import print_json


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "perf",
        help="Show recent CLI performance summary.",
    )
    p.add_argument(
        "-n", "--limit",
        type=int,
        default=50,
        help="Number of recent records to consider (default: 50)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return an exit code."""
    from aictl.core.perf import summary, read_recent

    if getattr(args, "json", False):
        print_json({
            "summary": summary(),
            "recent": [
                r.__dict__ for r in read_recent(limit=args.limit)
            ],
        })
        return 0

    summ = summary()
    if not summ:
        print("\n  No performance data yet. Run a few commands first.\n")
        return 0

    print()
    print("  Performance summary (recent activity)")
    print()
    print(f"  {'COMMAND':<18} {'COUNT':>6}  {'P50':>8}  {'P95':>8}  {'P99':>8}  {'FAIL':>5}")
    print(f"  {'-'*18} {'-'*6}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*5}")

    # Sort by p95 desc — slowest commands first
    items = sorted(summ.items(), key=lambda x: -x[1]["p95_ms"])
    for cmd, stats in items:
        p50 = f"{stats['p50_ms']:.0f}ms"
        p95 = f"{stats['p95_ms']:.0f}ms"
        p99 = f"{stats['p99_ms']:.0f}ms"
        fail_marker = f"{stats['failures']:>5}" if stats['failures'] else "    -"
        print(
            f"  {cmd:<18} {stats['count']:>6}  "
            f"{p50:>8}  {p95:>8}  {p99:>8}  {fail_marker}"
        )
    print()
    return 0
