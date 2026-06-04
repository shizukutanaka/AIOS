"""Cost estimator: GPU cloud vs on-prem TCO with real April 2026 pricing.

Calculates:
  - Cloud GPU cost per month (on-demand, reserved, spot)
  - On-premise hardware amortization
  - Cost per million tokens (CPM)
  - Break-even point (months)
  - Recommendation (cloud vs on-prem)

Pricing data: April 2026 market rates from multiple providers.
"""

from __future__ import annotations

from dataclasses import dataclass


# GPU cloud pricing ($/hr, on-demand, April 2026)
CLOUD_PRICING: dict[str, dict[str, float]] = {
    "RTX 4090": {
        "hyperbolic": 0.50, "runpod": 0.44, "vast.ai": 0.35,
    },
    "RTX 5090": {
        "runpod": 0.69, "vast.ai": 0.55,
    },
    "A100 80GB": {
        "hyperbolic": 1.80, "lambda": 1.89, "runpod": 1.64, "coreweave": 2.21,
    },
    "H100 SXM": {
        "hyperbolic": 3.20, "lambda": 2.49, "runpod": 3.49,
        "coreweave": 2.65, "gmi": 2.10, "aws": 12.30,
    },
    "H200 SXM": {
        "gmi": 2.50, "lambda": 3.29, "coreweave": 3.49,
    },
    "B200": {
        "spheron": 2.25, "coreweave": 8.60, "runpod": 4.88,
        "modal": 6.25, "lambda": 5.50,
    },
    "GB200": {
        "coreweave": 17.85, "lambda": 14.00,
    },
}

# On-premise hardware cost (USD, typical street price April 2026)
HARDWARE_COST: dict[str, int] = {
    "RTX 4090": 1600,
    "RTX 5090": 2000,
    "RTX 3090": 800,
    "A100 80GB": 15000,
    "H100 SXM": 30000,
    "H200 SXM": 35000,
    "B200": 50000,
    "GB200": 70000,
}

# Power consumption (watts)
POWER_WATTS: dict[str, int] = {
    "RTX 4090": 450,
    "RTX 5090": 575,
    "RTX 3090": 350,
    "A100 80GB": 400,
    "H100 SXM": 700,
    "H200 SXM": 700,
    "B200": 1000,
    "GB200": 1200,
}

# Inference throughput (tokens/sec, Llama 3.1 70B FP16, batch=256)
THROUGHPUT_70B: dict[str, int] = {
    "RTX 4090": 0,       # Can't run 70B FP16
    "RTX 5090": 0,       # 32GB VRAM, can't run 70B FP16
    "A100 80GB": 130,
    "H100 SXM": 280,
    "H200 SXM": 450,
    "B200": 700,          # ~2.5x H100 at FP8
    "GB200": 900,
}

# VRAM (GB)
VRAM_GB: dict[str, int] = {
    "RTX 4090": 24,
    "RTX 3090": 24,
    "A100 80GB": 80,
    "H100 SXM": 80,
    "H200 SXM": 141,
}


@dataclass
class CostEstimate:
    gpu_type: str
    num_gpus: int = 1
    hours_per_day: float = 24.0
    # Cloud
    cloud_monthly_usd: float = 0.0
    cloud_yearly_usd: float = 0.0
    cloud_provider: str = ""
    cloud_rate_hr: float = 0.0
    # On-prem
    onprem_hardware_usd: float = 0.0
    onprem_power_monthly_usd: float = 0.0
    onprem_monthly_usd: float = 0.0  # Amortized over 36 months + power
    # Comparison
    break_even_months: float = 0.0
    savings_3yr_usd: float = 0.0
    recommendation: str = ""
    # Tokens
    cost_per_million_tokens: float = 0.0
    monthly_token_capacity: int = 0


def estimate_cost(
    gpu_type: str = "H100 SXM",
    num_gpus: int = 1,
    hours_per_day: float = 24.0,
    electricity_per_kwh: float = 0.12,
    cloud_provider: str = "",
) -> CostEstimate:
    """Estimate cloud vs on-prem costs."""
    est = CostEstimate(gpu_type=gpu_type, num_gpus=num_gpus, hours_per_day=hours_per_day)

    # Cloud cost
    prices = CLOUD_PRICING.get(gpu_type, {})
    if cloud_provider and cloud_provider in prices:
        rate = prices[cloud_provider]
        est.cloud_provider = cloud_provider
    elif prices:
        # Use cheapest provider
        est.cloud_provider = min(prices, key=prices.get)
        rate = prices[est.cloud_provider]
    else:
        rate = 3.0  # Default fallback

    est.cloud_rate_hr = rate
    monthly_hours = hours_per_day * 30
    est.cloud_monthly_usd = rate * num_gpus * monthly_hours
    est.cloud_yearly_usd = est.cloud_monthly_usd * 12

    # On-prem cost
    hw_cost = HARDWARE_COST.get(gpu_type, 25000)
    est.onprem_hardware_usd = hw_cost * num_gpus

    watts = POWER_WATTS.get(gpu_type, 500)
    kwh_monthly = (watts * num_gpus * hours_per_day * 30) / 1000
    est.onprem_power_monthly_usd = kwh_monthly * electricity_per_kwh

    # Amortize hardware over 36 months
    hw_monthly = est.onprem_hardware_usd / 36
    est.onprem_monthly_usd = hw_monthly + est.onprem_power_monthly_usd

    # Break-even
    if est.cloud_monthly_usd > est.onprem_monthly_usd:
        monthly_savings = est.cloud_monthly_usd - est.onprem_monthly_usd
        if monthly_savings > 0:
            est.break_even_months = est.onprem_hardware_usd / monthly_savings
    else:
        est.break_even_months = 0  # Cloud is cheaper, don't buy

    # 3-year savings
    cloud_3yr = est.cloud_monthly_usd * 36
    onprem_3yr = est.onprem_hardware_usd + (est.onprem_power_monthly_usd * 36)
    est.savings_3yr_usd = cloud_3yr - onprem_3yr

    # Recommendation
    if hours_per_day < 4:
        est.recommendation = "cloud (low utilization)"
    elif est.break_even_months > 0 and est.break_even_months < 12:
        est.recommendation = f"on-prem (break-even in {est.break_even_months:.0f} months)"
    elif est.break_even_months > 12:
        est.recommendation = "cloud (long break-even)"
    else:
        est.recommendation = "on-prem (always cheaper)"

    # Token economics
    throughput = THROUGHPUT_70B.get(gpu_type, 0) * num_gpus
    if throughput > 0:
        tokens_per_hour = throughput * 3600
        if est.cloud_rate_hr > 0:
            est.cost_per_million_tokens = (est.cloud_rate_hr * num_gpus) / (tokens_per_hour / 1_000_000)
        est.monthly_token_capacity = int(tokens_per_hour * monthly_hours)

    return est


def compare_gpus(
    hours_per_day: float = 24.0,
    electricity_per_kwh: float = 0.12,
) -> list[CostEstimate]:
    """Compare all GPU types for the same workload."""
    results: list[CostEstimate] = []
    for gpu in CLOUD_PRICING:
        if gpu in HARDWARE_COST:
            est = estimate_cost(gpu_type=gpu, hours_per_day=hours_per_day,
                                electricity_per_kwh=electricity_per_kwh)
            results.append(est)
    return results
