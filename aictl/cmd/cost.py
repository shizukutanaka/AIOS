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
