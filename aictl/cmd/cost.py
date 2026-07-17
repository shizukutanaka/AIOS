"""aictl cost — GPU cost estimation and cloud vs on-prem comparison."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json, print_kv, print_table
from aictl.core.cost import estimate_cost, compare_gpus
from aictl.runtime.broker import full_detect


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("cost", help="GPU cost estimation")
    csub = p.add_subparsers(dest="cost_cmd")

    est = csub.add_parser("estimate", help="Estimate costs for your GPU")
    est.add_argument("--gpu", default="", help="GPU type (auto-detect if empty)")
    est.add_argument("--hours", type=float, default=24, help="Hours/day usage")
    est.add_argument("--gpus", type=int, default=1, help="Number of GPUs")
    est.set_defaults(func=run_estimate)

    cmp = csub.add_parser("compare", help="Compare all GPU types")
    cmp.add_argument("--hours", type=float, default=24, help="Hours/day usage")
    cmp.set_defaults(func=run_compare)

    bgt = csub.add_parser("budget", help="Check projected cost against a monthly budget")
    bgt.add_argument("--monthly-max", type=float, required=True, dest="monthly_max",
                     help="Monthly budget cap in JPY (or --currency USD)")
    bgt.add_argument("--currency", default="JPY", choices=["JPY", "USD"],
                     help="Currency for the threshold (default: JPY)")
    bgt.add_argument("--days", type=int, default=14,
                     help="Historical window for cost projection (default: 14)")
    bgt.add_argument("--json", action="store_true", help="JSON output")
    bgt.set_defaults(func=run_budget)

    forecast = csub.add_parser("forecast", help="Project costs over a future horizon")
    forecast.add_argument("--gpu", default="", help="GPU type (auto-detect if empty)")
    forecast.add_argument("--gpus", type=int, default=1, help="Number of GPUs")
    forecast.add_argument("--horizon", type=int, default=90,
                          help="Forecast horizon in days (default: 90)")
    forecast.add_argument("--hours", type=float, default=24, help="Hours/day usage")
    forecast.set_defaults(func=run_forecast)

    providers = csub.add_parser("providers", help="Show cheapest cloud providers per GPU")
    providers.add_argument("--hours", type=float, default=24, help="Hours/day usage")
    providers.set_defaults(func=run_providers)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_estimate(args: argparse.Namespace) -> int:
    """Execute the estimate subcommand."""
    gpu = getattr(args, "gpu", "")
    if not gpu:
        report = full_detect()
        if report.gpus:
            gpu = _map_gpu_name(report.gpus[0].name)
        else:
            gpu = "RTX 4090"

    est = estimate_cost(gpu_type=gpu, num_gpus=getattr(args, "gpus", 1),
                        hours_per_day=getattr(args, "hours", 24))

    if getattr(args, "json", False):
        print_json(est.__dict__)
        return 0

    ok(f"Cost Estimate — {est.gpu_type} x{est.num_gpus} ({est.hours_per_day}h/day)")
    print()
    print_kv([
        ("Cloud (cheapest)", f"${est.cloud_monthly_usd:,.0f}/mo ({est.cloud_provider} @ ${est.cloud_rate_hr:.2f}/hr)"),
        ("Cloud yearly", f"${est.cloud_yearly_usd:,.0f}"),
        ("On-prem hardware", f"${est.onprem_hardware_usd:,.0f}"),
        ("On-prem monthly", f"${est.onprem_monthly_usd:,.0f}/mo (amortized + power)"),
        ("Break-even", f"{est.break_even_months:.0f} months" if est.break_even_months > 0 else "Cloud cheaper"),
        ("3-year savings", f"${est.savings_3yr_usd:,.0f} (on-prem vs cloud)"),
        ("Recommendation", est.recommendation),
    ], indent=2)

    if est.cost_per_million_tokens > 0:
        print()
        print_kv([
            ("Cost/M tokens", f"${est.cost_per_million_tokens:.4f}"),
            ("Monthly capacity", f"{est.monthly_token_capacity:,} tokens"),
        ], indent=2)
    return 0


def run_compare(args: argparse.Namespace) -> int:
    """Execute the compare subcommand."""
    results = compare_gpus(hours_per_day=getattr(args, "hours", 24))

    if getattr(args, "json", False):
        print_json([r.__dict__ for r in results])
        return 0

    rows = [{"gpu": r.gpu_type,
             "cloud/mo": f"${r.cloud_monthly_usd:,.0f}",
             "onprem/mo": f"${r.onprem_monthly_usd:,.0f}",
             "breakeven": f"{r.break_even_months:.0f}mo" if r.break_even_months > 0 else "—",
             "$/M tok": f"${r.cost_per_million_tokens:.4f}" if r.cost_per_million_tokens > 0 else "—",
             "recommend": r.recommendation.split("(")[0].strip(),
             } for r in results]
    print_table(rows, ["gpu", "cloud/mo", "onprem/mo", "breakeven", "$/M tok", "recommend"])
    return 0


def run_budget(args: argparse.Namespace) -> int:
    """Check projected monthly cost against a budget threshold."""
    import time
    from collections import defaultdict
    from aictl.core.output import warn

    monthly_max = args.monthly_max
    currency = getattr(args, "currency", "JPY")
    window = max(1, getattr(args, "days", 14))

    # Convert USD threshold to JPY for internal calculations (1 USD ≈ 150 JPY)
    USD_JPY_RATE = 150.0
    threshold_jpy = monthly_max if currency == "JPY" else monthly_max * USD_JPY_RATE

    # Read perf records for cost projection (same approach as tco forecast)
    from aictl.core.perf import read_recent
    records = read_recent(limit=10000)

    # TCO config defaults (same as tco forecast)
    cfg = {
        "gpu_watts": 350,
        "kwh_rate_jpy": 25,
        "hardware_cost_jpy": 500_000,
        "hardware_life_years": 3,
    }
    daily_fixed = cfg["hardware_cost_jpy"] / (cfg["hardware_life_years"] * 365)

    by_date: dict[str, int] = defaultdict(int)
    for r in records:
        date_str = time.strftime("%Y-%m-%d", time.localtime(r.timestamp))
        by_date[date_str] += 1

    sorted_dates = sorted(by_date.keys())[-window:]
    if not sorted_dates:
        projected_jpy = 0.0
        avg_daily_jpy = 0.0
    else:
        daily_costs = []
        for date_str in sorted_dates:
            cmds = by_date[date_str]
            gpu_hours = cmds / 100 * 2
            elec = (cfg["gpu_watts"] / 1000) * gpu_hours * cfg["kwh_rate_jpy"]
            daily_costs.append(elec + daily_fixed)
        avg_daily_jpy = sum(daily_costs) / len(daily_costs)
        projected_jpy = avg_daily_jpy * 30

    projected_display = projected_jpy if currency == "JPY" else projected_jpy / USD_JPY_RATE
    symbol = "¥" if currency == "JPY" else "$"
    under_budget = projected_display <= monthly_max
    status = "ok" if under_budget else "exceeded"

    if getattr(args, "json", False):
        print_json({
            "status": status,
            "projected_monthly": round(projected_display, 2),
            "monthly_max": monthly_max,
            "currency": currency,
            "window_days": len(sorted_dates),
            "under_budget": under_budget,
        })
        return 0 if under_budget else 1

    icon = "✓" if under_budget else "✗"
    label = "under budget" if under_budget else "OVER BUDGET"
    print(f"\n  {icon} Budget check: {symbol}{projected_display:,.0f}/mo projected "
          f"vs {symbol}{monthly_max:,.0f}/mo limit — {label}\n")
    if not under_budget:
        warn(f"Projected monthly cost exceeds budget by "
             f"{symbol}{projected_display - monthly_max:,.0f}")
    return 0 if under_budget else 1


def run_forecast(args: argparse.Namespace) -> int:
    """Project GPU costs over a future horizon using current estimate."""
    gpu = getattr(args, "gpu", "")
    if not gpu:
        report = full_detect()
        if report.gpus:
            gpu = _map_gpu_name(report.gpus[0].name)
        else:
            gpu = "RTX 4090"

    est = estimate_cost(gpu_type=gpu, num_gpus=getattr(args, "gpus", 1),
                        hours_per_day=getattr(args, "hours", 24))
    horizon = max(1, getattr(args, "horizon", 90))

    daily_cloud = est.cloud_monthly_usd / 30
    daily_onprem = est.onprem_monthly_usd / 30

    # Distinct, sorted milestone days within the horizon (always include the
    # horizon itself). Using a set dedupes the case where the horizon coincides
    # with a fixed checkpoint, e.g. --horizon 30 or 60.
    milestone_days = sorted({d for d in (30, 60, horizon) if d <= horizon})
    milestones = [
        {
            "days": days,
            "cloud_usd": round(daily_cloud * days, 2),
            "onprem_usd": round(daily_onprem * days, 2),
            "delta_usd": round((daily_cloud - daily_onprem) * days, 2),
        }
        for days in milestone_days
    ]

    if getattr(args, "json", False):
        print_json({
            "gpu": gpu, "gpus": est.num_gpus, "horizon_days": horizon,
            "daily_cloud_usd": round(daily_cloud, 2),
            "daily_onprem_usd": round(daily_onprem, 2),
            "milestones": milestones,
            "recommendation": est.recommendation,
        })
        return 0

    ok(f"Cost Forecast — {gpu} x{est.num_gpus} ({horizon}-day horizon)")
    print()
    rows = [{"days": str(m["days"]),
             "cloud ($)": f"{m['cloud_usd']:,.0f}",
             "onprem ($)": f"{m['onprem_usd']:,.0f}",
             "cloud saves": f"+{m['delta_usd']:,.0f}" if m["delta_usd"] > 0 else f"{m['delta_usd']:,.0f}",
             } for m in milestones]
    print_table(rows, ["days", "cloud ($)", "onprem ($)", "cloud saves"])
    print()
    print(f"  Recommendation: {est.recommendation}")
    return 0


def run_providers(args: argparse.Namespace) -> int:
    """Show cheapest cloud provider per GPU type."""
    results = compare_gpus(hours_per_day=getattr(args, "hours", 24))

    provider_map: dict[str, list[dict]] = {}
    for r in results:
        p = r.cloud_provider
        if p not in provider_map:
            provider_map[p] = []
        provider_map[p].append(r)

    if getattr(args, "json", False):
        print_json([{
            "provider": r.cloud_provider,
            "gpu": r.gpu_type,
            "cloud_monthly_usd": r.cloud_monthly_usd,
            "cloud_rate_hr": r.cloud_rate_hr,
            "cost_per_million_tokens": r.cost_per_million_tokens,
        } for r in results])
        return 0

    ok("Cloud Providers — cheapest options per GPU")
    print()
    rows = [{
        "provider": r.cloud_provider,
        "gpu": r.gpu_type,
        "$/hr": f"${r.cloud_rate_hr:.2f}",
        "$/mo": f"${r.cloud_monthly_usd:,.0f}",
        "$/M tok": f"${r.cost_per_million_tokens:.4f}" if r.cost_per_million_tokens > 0 else "—",
    } for r in results]
    print_table(rows, ["provider", "gpu", "$/hr", "$/mo", "$/M tok"])
    return 0


def _map_gpu_name(name: str) -> str:
    """Execute map gpu name."""
    n = name.upper()
    if "H200" in n:
        return "H200 SXM"
    if "H100" in n:
        return "H100 SXM"
    if "A100" in n:
        return "A100 80GB"
    if "4090" in n:
        return "RTX 4090"
    return "H100 SXM"
