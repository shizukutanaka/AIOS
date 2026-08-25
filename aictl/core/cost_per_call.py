"""Per-request cost attribution — return cost metadata in every API response.

OpenRouter aggregates cost at account level.
LiteLLM needs Postgres + config to do per-team attribution.
We do it inline: every inference response carries exact cost metadata.

Pricing table (April 2026, USD per 1M tokens):
  - Local inference: electricity only (~$0.003/1M tok on RTX 4090)
  - Cloud fallback: actual API prices per model

The local cost calculation:
  Power draw (W) × inference time (h) × $/kWh × tokens / tokens_per_hour
"""

from __future__ import annotations

from typing import Any

import os
import sys
from dataclasses import dataclass


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    """Read a float tuning knob from the environment, *safely*.

    These are read at import time, so a naive ``float(os.environ[...])`` lets a
    malformed value (``AICTL_GPU_WATTS=abc``) raise ValueError during import —
    crashing the entire CLI/SDK before the error handler can run, for any
    command that imports this module. A negative value would also silently
    produce negative cost estimates. So: unset/empty → default; non-numeric or
    below ``minimum`` → warn on stderr and fall back to default (never crash,
    never silently accept a bad physical quantity). stderr keeps --json on
    stdout clean.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except ValueError:
        print(f"  warning: {name}={raw!r} is not a number; using default {default}",
              file=sys.stderr)
        return default
    if val < minimum:
        print(f"  warning: {name}={val} is below the minimum {minimum}; "
              f"using default {default}", file=sys.stderr)
        return default
    return val



# ── Pricing table ──────────────────────────────────────────

@dataclass(frozen=True)
class ModelPrice:
    """Per-model pricing in USD per million tokens."""
    input_per_m: float
    output_per_m: float
    notes: str = ""


# Cloud API prices (April 2026)
CLOUD_PRICES: dict[str, ModelPrice] = {
    # OpenAI
    "gpt-4o":              ModelPrice(5.00, 15.00),
    "gpt-4o-mini":         ModelPrice(0.15,  0.60),
    "gpt-4.1":             ModelPrice(2.00,  8.00),
    "o3":                  ModelPrice(10.00, 40.00),
    "o4-mini":             ModelPrice(1.10,  4.40),
    # Anthropic
    "claude-opus-4":       ModelPrice(15.00, 75.00),
    "claude-sonnet-4":     ModelPrice(3.00,  15.00),
    "claude-haiku-3-5":    ModelPrice(0.80,   4.00),
    # Google
    "gemini-2.5-pro":      ModelPrice(1.25,  10.00),
    "gemini-2.0-flash":    ModelPrice(0.10,   0.40),
    # Open source via API
    "deepseek-v3":         ModelPrice(0.27,   1.10),
    "deepseek-r1":         ModelPrice(0.55,   2.19),
    "llama4-maverick":     ModelPrice(0.18,   0.59),
}

# Local inference electricity cost estimate
# Assumes RTX 4090 @ 450W, 8 tok/s average (mixed models)
# ¥27/kWh Tokyo → ~$0.18/kWh
_LOCAL_WATTS = _env_float("AICTL_GPU_WATTS", 450.0, minimum=0.0)
_KWH_RATE_USD = _env_float("AICTL_KWH_RATE_USD", 0.18, minimum=0.0)
_LOCAL_TOKENS_PER_HOUR = _env_float("AICTL_TOKENS_PER_HOUR", 28800.0, minimum=1.0)

# USD per local token
_LOCAL_COST_PER_TOKEN = (
    (_LOCAL_WATTS / 1000) * _KWH_RATE_USD / _LOCAL_TOKENS_PER_HOUR
)


@dataclass
class CallCost:
    """Cost for one inference call."""
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    cost_jpy: float      # approximate (1 USD ≈ 150 JPY, April 2026)
    cost_source: str     # "local" | "cloud:<model>"
    cost_per_1m_usd: float  # effective rate

    def as_dict(self) -> dict[str, Any]:
        """Execute as dict."""
        return {
            "input_tokens":   self.input_tokens,
            "output_tokens":  self.output_tokens,
            "total_tokens":   self.total_tokens,
            "cost_usd":       round(self.cost_usd, 8),
            "cost_jpy":       round(self.cost_jpy, 4),
            "cost_source":    self.cost_source,
            "cost_per_1m_usd": round(self.cost_per_1m_usd, 4),
        }


def compute(
    model: str,
    input_tokens: int,
    output_tokens: int,
    is_local: bool = True,
) -> CallCost:
    """Compute the cost for one inference call.

    Args:
        model: Model name (used to look up cloud prices).
        input_tokens: Number of input/prompt tokens.
        output_tokens: Number of output/completion tokens.
        is_local: If True, use electricity-based local cost.

    Returns:
        CallCost with full cost breakdown.
    """
    total = input_tokens + output_tokens
    usd_per_jpy = 150.0  # approximate

    if is_local:
        cost_usd = total * _LOCAL_COST_PER_TOKEN
        per_m = _LOCAL_COST_PER_TOKEN * 1_000_000
        source = "local"
    else:
        # Find cloud price (prefix match for versioned model names)
        price = _find_price(model)
        if price:
            cost_usd = (
                input_tokens / 1_000_000 * price.input_per_m
                + output_tokens / 1_000_000 * price.output_per_m
            )
            per_m = (price.input_per_m + price.output_per_m) / 2
            source = f"cloud:{model}"
        else:
            # Unknown model — use a conservative estimate
            cost_usd = total / 1_000_000 * 5.0  # $5/1M average
            per_m = 5.0
            source = f"cloud:{model}:estimated"

    return CallCost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        cost_usd=cost_usd,
        cost_jpy=cost_usd * usd_per_jpy,
        cost_source=source,
        cost_per_1m_usd=per_m,
    )


def _find_price(model: str) -> ModelPrice | None:
    """Prefix-match model name in the price table."""
    model_lower = model.lower()
    # Exact match first
    if model_lower in CLOUD_PRICES:
        return CLOUD_PRICES[model_lower]
    # Prefix match
    for key, price in CLOUD_PRICES.items():
        if model_lower.startswith(key) or key in model_lower:
            return price
    return None


# Below this, six decimal places render as $0.000000 — showing a real cost as
# free. Say "less than" rather than inventing a zero.
COST_DISPLAY_FLOOR = 0.000001


def format_usd(amount: float, cached: bool = False) -> str:
    """A USD cost string that means exactly one thing.

    Sub-$0.0001 costs used to render as `f"${amount * 1000:.4f}m"` — millidollars
    with a bare `m`. Two problems, and they compounded. The unit is ambiguous:
    "$0.0470m" reads as millions at least as readily as thousandths, and the
    other call site's comment called it "millicents", which it is not. And the
    README documented `print(r.cost)  # "$0.000047"` — an output the code could
    not produce, because 0.000047 came out as "$0.0470m".

    Worse at the bottom of the range: a genuinely free response (the in-process
    mock, which is what zero-config gives a new user) printed "$0.0000m".

    Six decimals cover everything the milli form did, in dollars, with no suffix
    to misread. Anything truly below display resolution says so instead of
    rounding to a confident zero.
    """
    if cached:
        return "$0.000000 (cached)"
    if amount <= 0:
        return "$0.000000"
    if amount < COST_DISPLAY_FLOOR:
        return f"<${COST_DISPLAY_FLOOR:.6f}"
    return f"${amount:.6f}"


def format_cost(cost: CallCost, currency: str = "usd") -> str:
    """Human-readable cost string."""
    if currency.lower() == "jpy":
        return f"¥{cost.cost_jpy:.4f}"
    return format_usd(cost.cost_usd)
