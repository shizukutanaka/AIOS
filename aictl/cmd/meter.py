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

    report = msub.add_parser("report", help="Cost attribution report per entity")
    report.add_argument("--sort", default="cost",
                        choices=["cost", "tokens", "entity"],
                        help="Sort by field (default: cost)")
    report.set_defaults(func=run_report)

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


def run_report(args: argparse.Namespace) -> int:
    """Cost attribution report: tokens + cost + quota utilization per entity."""
    import time as _time
    meter = TokenMeter()
    buckets = meter.list_usage()

    if not buckets:
        print("No usage recorded yet.")
        return 0

    now = _time.time()
    rows = []
    for b in buckets:
        cost = meter.estimate_cost(b.entity_id)
        # Projected monthly cost: extrapolate from days since first request
        days_active = max((now - b.first_request_at) / 86400, 1) if b.first_request_at else 1
        daily_avg_tokens = b.total_tokens / days_active
        projected_tokens = daily_avg_tokens * 30
        projected_cost = round(
            (projected_tokens / 1_000_000) * 0.15 +
            (projected_tokens * 0.6 / (b.prompt_tokens + 1)) / 1_000_000 * 0.60,
            4,
        )

        quota_pct = ""
        if b.quota_tokens_per_month > 0:
            pct = min(b.tokens_this_month / b.quota_tokens_per_month * 100, 999)
            quota_pct = f"{pct:.0f}%"

        rows.append({
            "entity": b.entity_id,
            "type": b.entity_type,
            "total_tokens": b.total_tokens,
            "this_month": b.tokens_this_month,
            "cost_usd": f"${cost:.4f}",
            "proj_month": f"${projected_cost:.4f}",
            "quota": quota_pct,
        })

    sort_key = getattr(args, "sort", "cost")
    if sort_key == "cost":
        rows.sort(key=lambda r: float(r["cost_usd"].lstrip("$")), reverse=True)
    elif sort_key == "tokens":
        rows.sort(key=lambda r: r["total_tokens"], reverse=True)
    elif sort_key == "entity":
        rows.sort(key=lambda r: r["entity"])

    if getattr(args, "json", False):
        print_json(rows)
        return 0

    print_table(rows, ["entity", "type", "total_tokens", "this_month",
                        "cost_usd", "proj_month", "quota"])
    total_cost = sum(float(r["cost_usd"].lstrip("$")) for r in rows)
    print(f"\n  Total cost: ${total_cost:.4f} USD across {len(rows)} entities")
    return 0
