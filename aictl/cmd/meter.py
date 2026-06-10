"""aictl meter — token usage metering and quota management."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json, print_table
from aictl.core.metering import TokenMeter


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("meter", help="Token usage metering")
    msub = p.add_subparsers(dest="meter_cmd")

    usage = msub.add_parser("usage", help="Show token usage")
    usage.add_argument("--entity", default="", help="Filter by entity ID")
    usage.set_defaults(func=run_usage)

    quota = msub.add_parser("quota", help="Set token quota")
    quota.add_argument("entity", help="Entity ID (apikey or tenant)")
    quota.add_argument("--per-day", type=int, default=None)
    quota.add_argument("--per-month", type=int, default=None)
    quota.set_defaults(func=run_quota)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_usage(args: argparse.Namespace) -> int:
    """Execute the usage subcommand."""
    meter = TokenMeter()
    buckets = meter.list_usage()

    entity = getattr(args, "entity", "")
    if entity:
        buckets = [b for b in buckets if b.entity_id == entity]

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json([asdict(b) for b in buckets])
        return 0

    if not buckets:
        print("No usage recorded yet.")
        return 0

    rows = [{"entity": b.entity_id, "requests": b.request_count,
             "prompt": f"{b.prompt_tokens:,}", "completion": f"{b.completion_tokens:,}",
             "total": f"{b.total_tokens:,}", "today": f"{b.tokens_today:,}",
             "cost": f"${meter.estimate_cost(b.entity_id):.4f}"}
            for b in buckets]
    print_table(rows, ["entity", "requests", "prompt", "completion", "total", "today", "cost"])
    return 0


def run_quota(args: argparse.Namespace) -> int:
    """Execute the quota subcommand."""
    meter = TokenMeter()
    meter.set_quota(args.entity,
                    per_day=getattr(args, "per_day", None),
                    per_month=getattr(args, "per_month", None))
    ok(f"Quota set for {args.entity}")
    return 0
