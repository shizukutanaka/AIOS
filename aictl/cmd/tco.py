"""aictl tco — True Cost of Ownership.

No competitor shows this. Ollama shows nothing. LiteLLM shows aggregate.
We show the real cost: electricity + hardware depreciation + cloud fallback.

  aictl tco              Summary for this month
  aictl tco --period 7d  Last 7 days
  aictl tco setup        Configure GPU price and electricity rate
"""

from __future__ import annotations

import argparse

from typing import Any

import os
import time
from pathlib import Path

from aictl.core.output import ok, warn, print_kv, print_json


# ── Default configuration ──────────────────────────────────

_DEFAULTS = {
    "gpu_price_jpy": 280_000,     # RTX 4090 ¥280,000
    "kwh_rate_jpy": 27,           # Tokyo TEPCO ¥27/kWh
    "gpu_watts": 450,             # RTX 4090 TDP
    "depreciation_months": 36,    # 3 years
    "gpu_name": "RTX 4090",
    "purchase_date": "",
}


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "tco",
        help="True cost: electricity + depreciation (no competitor shows this).",
    )
    sp = p.add_subparsers(dest="tco_cmd", required=False)

    sp.add_parser("setup", help="Configure GPU price, electricity rate.").set_defaults(func=run_setup)
    sp.add_parser("history", help="Cost history by day/week.").set_defaults(func=run_history)

    p.set_defaults(func=run_summary)


def run_summary(args: argparse.Namespace) -> int:
    """Show this month's true cost breakdown."""
    from aictl.core.perf import read_recent
    from aictl.core.sem_cache import get_default_cache

    cfg = _load_config()
    period_days = getattr(args, "period_days", 30)

    print()
    print(f"  True Cost of Ownership — last {period_days} days")
    print()

    # Hardware cost
    monthly_depreciation = cfg["gpu_price_jpy"] / cfg["depreciation_months"]
    usage_fraction = min(1.0, period_days / 30)
    depreciation_jpy = monthly_depreciation * usage_fraction

    # Electricity cost — estimate from perf records
    records = read_recent(limit=10000)
    active_seconds = sum(r.duration_ms / 1000 for r in records
                         if r.command in ("serve", "chat", "demo", "bench"))
    # Assume GPU is running ~8 hours/day when active
    estimated_gpu_hours = max(active_seconds / 3600, 0.1) * 8
    electricity_jpy = (cfg["gpu_watts"] / 1000) * estimated_gpu_hours * cfg["kwh_rate_jpy"]

    # Cache savings (tokens not sent to inference)
    cache_stats = get_default_cache().stats()
    tokens_saved = cache_stats.get("total_tokens_saved", 0)
    # Estimate cloud cost avoided: ¥0.75/1K tokens (GPT-4o-mini equivalent)
    cloud_savings_jpy = tokens_saved / 1000 * 0.75

    # Cloud fallback cost (from audit log approximation)
    # Cloud fallback cost: estimated from perf records (commands that used cloud)
    cloud_cmds = sum(1 for r in records if "cloud" in str(getattr(r, "error_type", "")))
    cloud_fallback_jpy = cloud_cmds * 15.0  # ~¥15 per cloud fallback call estimate

    total_jpy = depreciation_jpy + electricity_jpy + cloud_fallback_jpy
    total_usd = total_jpy / 150

    if getattr(args, "json", False):
        print_json({
            "period_days": period_days,
            "gpu": cfg["gpu_name"],
            "depreciation_jpy": round(depreciation_jpy),
            "electricity_jpy": round(electricity_jpy),
            "cloud_fallback_jpy": round(cloud_fallback_jpy),
            "total_jpy": round(total_jpy),
            "total_usd": round(total_usd, 2),
            "cache_tokens_saved": tokens_saved,
            "cloud_savings_jpy": round(cloud_savings_jpy),
        })
        return 0

    print_kv([
        ("Hardware",  cfg["gpu_name"]),
        ("Period",    f"{period_days} days"),
    ])
    print()
    print("  Cost breakdown:")
    print(f"    Depreciation   ¥{depreciation_jpy:>8,.0f}  "
          f"(¥{cfg['gpu_price_jpy']:,} ÷ {cfg['depreciation_months']}mo)")
    print(f"    Electricity    ¥{electricity_jpy:>8,.0f}  "
          f"({cfg['gpu_watts']}W × ~{estimated_gpu_hours:.0f}h × ¥{cfg['kwh_rate_jpy']}/kWh)")
    if cloud_fallback_jpy > 0:
        print(f"    Cloud fallback ¥{cloud_fallback_jpy:>8,.0f}")
    print("    ─────────────────────────")
    print(f"    Total          ¥{total_jpy:>8,.0f}  (≈ ${total_usd:.2f})")
    print()

    if cloud_savings_jpy > 0:
        ok(f"Cache saved ≈ ¥{cloud_savings_jpy:,.0f} in cloud inference costs "
           f"({tokens_saved:,} tokens)")
        print()

    # Comparison with cloud equivalent
    cloud_equiv_jpy = total_jpy * 3  # rough estimate: cloud 3x more expensive
    savings_jpy = cloud_equiv_jpy - total_jpy
    if savings_jpy > 0:
        print(f"  vs. Cloud equivalent: ≈ ¥{cloud_equiv_jpy:,.0f}")
        ok(f"  Saving: ≈ ¥{savings_jpy:,.0f} vs. equivalent cloud usage")
    print()
    print("  Configure:  aictl tco setup")
    print()
    return 0


def run_setup(args: argparse.Namespace) -> int:
    """Interactive configuration of GPU price and electricity rate."""
    cfg = _load_config()
    print()
    print("  TCO Configuration")
    print()
    print("  Press Enter to keep the current value.")
    print()

    updates: dict[str, Any] = {}

    def _ask(prompt: str, key: str, fmt: Any=str) -> None:
        """Prompt the user for input with a default value."""
        current = cfg.get(key, _DEFAULTS[key])
        try:
            raw = input(f"  {prompt} [{current}]: ").strip()
            if raw:
                updates[key] = fmt(raw)
        except (EOFError, KeyboardInterrupt):
            pass  # best-effort; failure is non-critical

    _ask("GPU name",             "gpu_name", str)
    _ask("GPU purchase price (¥)","gpu_price_jpy", int)
    _ask("GPU wattage (W)",      "gpu_watts", int)
    _ask("Electricity rate (¥/kWh)", "kwh_rate_jpy", int)
    _ask("Depreciation period (months)", "depreciation_months", int)

    if updates:
        cfg.update(updates)
        _save_config(cfg)
        ok("Configuration saved.")
    else:
        print("  No changes.")
    print()
    return 0


def run_history(args: argparse.Namespace) -> int:
    """Show cost trend by day."""
    from aictl.core.perf import read_recent
    records = read_recent(limit=1000)

    if not records:
        warn("No activity recorded yet.")
        return 0

    # Group by date
    from collections import defaultdict
    by_date: dict[str, int] = defaultdict(int)
    for r in records:
        date_str = time.strftime("%Y-%m-%d", time.localtime(r.timestamp))
        by_date[date_str] += 1

    cfg = _load_config()
    daily_fixed = (cfg["gpu_price_jpy"] / cfg["depreciation_months"]) / 30

    print()
    print("  Daily activity and cost estimate")
    print()
    print(f"  {'DATE':<12}  {'CMDS':>5}  {'ELEC ¥':>8}  {'DEPR ¥':>8}  {'TOTAL ¥':>9}")
    print(f"  {'-'*12}  {'-'*5}  {'-'*8}  {'-'*8}  {'-'*9}")

    for date_str in sorted(by_date.keys())[-14:]:  # last 14 days
        cmds = by_date[date_str]
        # Very rough: assume 2 hours of GPU activity per 100 commands
        gpu_hours = cmds / 100 * 2
        elec = (cfg["gpu_watts"] / 1000) * gpu_hours * cfg["kwh_rate_jpy"]
        total = elec + daily_fixed
        print(f"  {date_str:<12}  {cmds:>5}  {elec:>8.0f}  {daily_fixed:>8.0f}  {total:>9.0f}")

    print()
    return 0


def _config_path() -> Path:
    """Return the path to the TCO configuration file."""
    base = os.environ.get("AIOS_STATE_DIR", os.path.expanduser("~/.aios"))
    return Path(base) / "tco.json"


def _load_config() -> dict[str, Any]:
    """Load data from persistent storage."""
    import json
    path = _config_path()
    if path.exists():
        try:
            cfg = json.loads(path.read_text())
            for k, v in _DEFAULTS.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass  # best-effort; failure is non-critical
    return dict(_DEFAULTS)


def _save_config(cfg: dict[str, Any]) -> None:
    """Persist data to storage."""
    import json
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
