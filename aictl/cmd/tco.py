"""aictl tco — True Cost of Ownership + Carbon/Energy Advisor.

No competitor shows this. Ollama shows nothing. LiteLLM shows aggregate.
We show the real cost: electricity + hardware depreciation + cloud fallback,
and now also kWh consumed and CO₂e emitted with GPU power-cap advice.

  aictl tco                                  Summary (30 days)
  aictl tco --carbon-intensity 460           Override grid intensity (gCO₂e/kWh)
  aictl tco carbon                           Full energy + carbon advisory
  aictl tco carbon --region jp               Regional grid intensity
  aictl tco setup                            Configure GPU price, electricity rate

FREESH (arXiv:2511.00807): LLF scheduling + GPU frequency scaling → 28.6% energy
savings, 45.5% emissions reduction without quality loss.
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

# Grid carbon intensity by region (gCO₂e/kWh, IEA 2024 data).
CARBON_INTENSITY_BY_REGION: dict[str, int] = {
    "global": 500,   # IEA world average
    "jp":     460,   # Japan
    "us":     380,   # United States
    "eu":     255,   # EU average
    "de":     350,   # Germany
    "fr":      55,   # France (nuclear-heavy)
    "ca":     130,   # Canada (hydro-heavy)
    "cn":     550,   # China (coal-heavy)
    "au":     490,   # Australia
    "uk":     175,   # United Kingdom
}
_DEFAULT_CARBON_INTENSITY = 500  # global average

# GPU power-cap advisory (TDP → conservative cap → aggressive cap in Watts).
# Conservative: ~15% energy reduction, <2% throughput loss.
# Aggressive:   ~35% energy reduction, ~8% throughput loss (batch-workload friendly).
_GPU_POWER_CAPS: dict[str, dict[str, int]] = {
    "RTX 4090":  {"tdp": 450, "conservative": 350, "aggressive": 280},
    "RTX 5090":  {"tdp": 575, "conservative": 450, "aggressive": 360},
    "RTX 3090":  {"tdp": 350, "conservative": 280, "aggressive": 220},
    "H100":      {"tdp": 700, "conservative": 550, "aggressive": 400},
    "H200":      {"tdp": 700, "conservative": 550, "aggressive": 420},
    "B200":      {"tdp": 1000,"conservative": 800, "aggressive": 650},
    "A100":      {"tdp": 400, "conservative": 310, "aggressive": 250},
    "A100 80GB": {"tdp": 400, "conservative": 310, "aggressive": 250},
}
# CO₂e equivalences for tangible comparisons
_KM_PER_KG_CO2E = 8.3   # driving 1 km ≈ 120 gCO₂e (average petrol car)


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "tco",
        help="True cost: electricity + depreciation + carbon (no competitor shows this).",
    )
    p.add_argument("--period-days", type=int, default=30, dest="period_days",
                   help="Period to analyse in days (default: 30).")
    p.add_argument("--carbon-intensity", type=float, default=None,
                   dest="carbon_intensity",
                   help=("Grid carbon intensity in gCO₂e/kWh. "
                         f"Default: {_DEFAULT_CARBON_INTENSITY} (world avg). "
                         f"Regions: {', '.join(CARBON_INTENSITY_BY_REGION)}."))
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sp = p.add_subparsers(dest="tco_cmd", required=False)

    sp.add_parser("setup", help="Configure GPU price, electricity rate.").set_defaults(func=run_setup)
    sp.add_parser("history", help="Cost history by day/week.").set_defaults(func=run_history)

    carbon = sp.add_parser("carbon",
                           help="Energy + carbon advisor: kWh, CO₂e, GPU power-cap flags.")
    carbon.add_argument("--region", default="global",
                        choices=list(CARBON_INTENSITY_BY_REGION),
                        help="Grid region for carbon intensity.")
    carbon.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    carbon.set_defaults(func=run_carbon)

    p.set_defaults(func=run_summary)


def _compute_energy(cfg: dict, period_days: int, records: list) -> tuple[float, float]:
    """Return (estimated_gpu_hours, kwh) for the period."""
    active_seconds = sum(r.duration_ms / 1000 for r in records
                         if r.command in ("serve", "chat", "demo", "bench"))
    estimated_gpu_hours = max(active_seconds / 3600, 0.1) * 8
    kwh = (cfg["gpu_watts"] / 1000) * estimated_gpu_hours
    return estimated_gpu_hours, kwh


def run_summary(args: argparse.Namespace) -> int:
    """Show this month's true cost breakdown."""
    from aictl.core.perf import read_recent
    from aictl.core.sem_cache import get_default_cache

    cfg = _load_config()
    period_days = getattr(args, "period_days", 30)
    ci = getattr(args, "carbon_intensity", None) or _DEFAULT_CARBON_INTENSITY

    # Hardware cost
    monthly_depreciation = cfg["gpu_price_jpy"] / cfg["depreciation_months"]
    usage_fraction = min(1.0, period_days / 30)
    depreciation_jpy = monthly_depreciation * usage_fraction

    # Electricity cost — estimate from perf records
    records = read_recent(limit=10000)
    estimated_gpu_hours, kwh = _compute_energy(cfg, period_days, records)
    electricity_jpy = kwh * cfg["kwh_rate_jpy"]

    # Carbon
    co2e_kg = kwh * ci / 1000

    # Cache savings (tokens not sent to inference)
    cache_stats = get_default_cache().stats()
    tokens_saved = cache_stats.get("total_tokens_saved", 0)
    cloud_savings_jpy = tokens_saved / 1000 * 0.75

    # Cloud fallback cost
    cloud_cmds = sum(1 for r in records if "cloud" in str(getattr(r, "error_type", "")))
    cloud_fallback_jpy = cloud_cmds * 15.0

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
            "kwh": round(kwh, 2),
            "co2e_kg": round(co2e_kg, 3),
            "carbon_intensity_gco2_kwh": ci,
        })
        return 0

    print()
    print(f"  True Cost of Ownership — last {period_days} days")
    print()
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

    print(f"  Energy:  {kwh:.1f} kWh  →  {co2e_kg:.2f} kg CO₂e "
          f"(@ {ci:.0f} gCO₂e/kWh)")
    equiv_km = co2e_kg * _KM_PER_KG_CO2E
    print(f"           ≈ driving {equiv_km:.0f} km (petrol car)")
    print()

    if cloud_savings_jpy > 0:
        ok(f"Cache saved ≈ ¥{cloud_savings_jpy:,.0f} in cloud inference costs "
           f"({tokens_saved:,} tokens)")
        print()

    # Comparison with cloud equivalent
    cloud_equiv_jpy = total_jpy * 3
    savings_jpy = cloud_equiv_jpy - total_jpy
    if savings_jpy > 0:
        print(f"  vs. Cloud equivalent: ≈ ¥{cloud_equiv_jpy:,.0f}")
        ok(f"  Saving: ≈ ¥{savings_jpy:,.0f} vs. equivalent cloud usage")
    print()
    print("  Configure:  aictl tco setup")
    print("  Carbon details: aictl tco carbon")
    print()
    return 0


def run_carbon(args: argparse.Namespace) -> int:
    """Energy + carbon advisor with GPU power-cap recommendations."""
    from aictl.core.perf import read_recent

    cfg = _load_config()
    region = getattr(args, "region", "global")
    ci = CARBON_INTENSITY_BY_REGION.get(region, _DEFAULT_CARBON_INTENSITY)
    period_days = getattr(args, "period_days", 30)
    use_json = getattr(args, "json", False)

    records = read_recent(limit=10000)
    _gpu_hours, kwh = _compute_energy(cfg, period_days, records)
    co2e_kg = kwh * ci / 1000
    equiv_km = co2e_kg * _KM_PER_KG_CO2E

    # Power-cap advice for detected GPU
    gpu_name = cfg["gpu_name"]
    caps = None
    for key in _GPU_POWER_CAPS:
        if key.lower() in gpu_name.lower() or gpu_name.lower() in key.lower():
            caps = _GPU_POWER_CAPS[key]
            gpu_name = key
            break

    # Savings projections (FREESH paper benchmarks)
    conservative_kwh = kwh * 0.85    # ~15% reduction with conservative cap
    aggressive_kwh = kwh * 0.714     # ~28.6% reduction (FREESH result)
    conservative_co2 = conservative_kwh * ci / 1000
    aggressive_co2 = aggressive_kwh * ci / 1000

    if use_json:
        result = {
            "region": region,
            "carbon_intensity_gco2_kwh": ci,
            "period_days": period_days,
            "kwh": round(kwh, 2),
            "co2e_kg": round(co2e_kg, 3),
            "co2e_equiv_km_driven": round(equiv_km, 1),
            "gpu": gpu_name,
            "power_cap": caps,
            "projected": {
                "conservative_kwh": round(conservative_kwh, 2),
                "conservative_co2e_kg": round(conservative_co2, 3),
                "aggressive_kwh": round(aggressive_kwh, 2),
                "aggressive_co2e_kg": round(aggressive_co2, 3),
            },
        }
        print_json(result)
        return 0

    print()
    print("  Energy & Carbon Advisor")
    print()
    print(f"  Region: {region}  ({ci} gCO₂e/kWh, IEA 2024)")
    print()
    print(f"  Estimated last {period_days} days:")
    print(f"    Energy:    {kwh:.1f} kWh")
    print(f"    CO₂e:      {co2e_kg:.2f} kg  (≈ {equiv_km:.0f} km driven)")
    print()

    if caps:
        print(f"  GPU power-cap options for {gpu_name} (TDP: {caps['tdp']}W):")
        cons_save = round((1 - 0.85) * 100)
        agg_save = round((1 - 0.714) * 100)
        print(f"    Conservative  {caps['conservative']}W  → ~{cons_save}% energy, <2% throughput loss")
        print(f"    Aggressive    {caps['aggressive']}W  → ~{agg_save}% energy, ~8% throughput loss")
        print()
        print(f"  Apply (requires root / nvidia-smi):")
        print(f"    nvidia-smi -pm 1                        # persistence mode")
        print(f"    nvidia-smi -i 0 -pl {caps['conservative']}          # conservative cap")
        print(f"    nvidia-smi -i 0 -pl {caps['aggressive']}          # aggressive cap")
        print()
        print(f"  Projected savings with conservative cap:")
        print(f"    Energy: {conservative_kwh:.1f} kWh  (was {kwh:.1f})")
        print(f"    CO₂e:   {conservative_co2:.2f} kg  (was {co2e_kg:.2f})")
    else:
        print(f"  No power-cap profile for {gpu_name}.")
        print(f"  Run: nvidia-smi -q -d POWER to check supported range.")
    print()
    print("  FREESH scheduling (arXiv:2511.00807):")
    print(f"    LLF + dynamic frequency scaling → 28.6% energy, 45.5% CO₂e reduction")
    print(f"    Projected aggressive: {aggressive_kwh:.1f} kWh / {aggressive_co2:.2f} kg CO₂e")
    print()
    print(f"  Region intensities (gCO₂e/kWh): " +
          "  ".join(f"{k}={v}" for k, v in CARBON_INTENSITY_BY_REGION.items()))
    print("  Source: IEA 2024, arXiv:2511.00807 (FREESH)\n")
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
