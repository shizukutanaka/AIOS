"""Token metering: per-tenant and per-apikey token tracking with quotas.

LLM inference costs scale with tokens, not CPU-hours. This module provides:
  - Token counting per API key and tenant
  - Configurable quotas (tokens/day, tokens/month)
  - Rate limiting (tokens/minute)
  - Cost attribution (tokens × price-per-token)
  - Quota enforcement (reject requests when exceeded)

Storage: ~/.aios/metering.json (rotated daily)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class TokenBucket:
    """Token usage tracking for a single entity (apikey or tenant)."""
    entity_id: str
    entity_type: str = "apikey"      # apikey | tenant
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
    first_request_at: float = 0.0
    last_request_at: float = 0.0
    # Quotas
    quota_tokens_per_day: int = 0    # 0 = unlimited
    quota_tokens_per_month: int = 0
    quota_tokens_per_minute: int = 0
    # Daily/monthly/minute tracking
    tokens_today: int = 0
    tokens_this_month: int = 0
    tokens_this_minute: int = 0
    today_date: str = ""
    month_date: str = ""
    minute_start: float = 0.0        # epoch seconds of the current 60s window


@dataclass
class MeteringRecord:
    """Single metering event."""
    timestamp: float
    entity_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float = 0.0


class TokenMeter:
    """Track and enforce token usage quotas."""

    def __init__(self, state_dir: Path | None = None):
        """Initialize token meter with state directory."""
        if state_dir is None:
            from aictl.core.state import DEFAULT_STATE_DIR
            state_dir = DEFAULT_STATE_DIR
        self.dir = state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._buckets_path = self.dir / "metering.json"
        self._log_path = self.dir / "metering_log.jsonl"

    def record(self, entity_id: str, model: str,
               prompt_tokens: int, completion_tokens: int,
               latency_ms: float = 0.0,
               entity_type: str = "apikey") -> bool:
        """Record token usage. Returns False if quota exceeded."""
        total = prompt_tokens + completion_tokens
        now = time.time()
        today = time.strftime("%Y-%m-%d")
        month = time.strftime("%Y-%m")

        # Load bucket
        buckets = self._load_buckets()
        bucket = buckets.get(entity_id)
        if bucket is None:
            bucket = TokenBucket(entity_id=entity_id, entity_type=entity_type,
                                 first_request_at=now)
            buckets[entity_id] = bucket

        # Reset daily/monthly/minute counters
        if bucket.today_date != today:
            bucket.tokens_today = 0
            bucket.today_date = today
        if bucket.month_date != month:
            bucket.tokens_this_month = 0
            bucket.month_date = month
        if bucket.minute_start == 0.0 or now - bucket.minute_start >= 60:
            bucket.minute_start = now
            bucket.tokens_this_minute = 0

        # Check quotas BEFORE recording
        if bucket.quota_tokens_per_day > 0:
            if bucket.tokens_today + total > bucket.quota_tokens_per_day:
                return False  # Daily quota exceeded

        if bucket.quota_tokens_per_month > 0:
            if bucket.tokens_this_month + total > bucket.quota_tokens_per_month:
                return False  # Monthly quota exceeded

        if bucket.quota_tokens_per_minute > 0:
            if bucket.tokens_this_minute + total > bucket.quota_tokens_per_minute:
                return False  # Per-minute rate limit exceeded

        # Record
        bucket.prompt_tokens += prompt_tokens
        bucket.completion_tokens += completion_tokens
        bucket.total_tokens += total
        bucket.tokens_today += total
        bucket.tokens_this_month += total
        bucket.tokens_this_minute += total
        bucket.request_count += 1
        bucket.last_request_at = now

        self._save_buckets(buckets)

        # Append to log
        record = MeteringRecord(
            timestamp=now, entity_id=entity_id, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=total, latency_ms=latency_ms,
        )
        with open(self._log_path, "a") as f:
            f.write(json.dumps(asdict(record)) + "\n")

        return True

    def get_usage(self, entity_id: str) -> TokenBucket | None:
        """Get usage."""
        buckets = self._load_buckets()
        return buckets.get(entity_id)

    def set_quota(self, entity_id: str, *,
                  per_day: int = 0, per_month: int = 0,
                  per_minute: int = 0) -> None:
        """Set token quotas for an entity."""
        buckets = self._load_buckets()
        bucket = buckets.get(entity_id)
        if bucket is None:
            bucket = TokenBucket(entity_id=entity_id)
            buckets[entity_id] = bucket
        if per_day:
            bucket.quota_tokens_per_day = per_day
        if per_month:
            bucket.quota_tokens_per_month = per_month
        if per_minute:
            bucket.quota_tokens_per_minute = per_minute
        self._save_buckets(buckets)

    def list_usage(self) -> list[TokenBucket]:
        """List usage."""
        return list(self._load_buckets().values())

    def estimate_cost(self, entity_id: str,
                      price_per_million_input: float = 0.15,
                      price_per_million_output: float = 0.60) -> float:
        """Estimate cost in USD for an entity's usage."""
        bucket = self.get_usage(entity_id)
        if bucket is None:
            return 0.0
        input_cost = (bucket.prompt_tokens / 1_000_000) * price_per_million_input
        output_cost = (bucket.completion_tokens / 1_000_000) * price_per_million_output
        return round(input_cost + output_cost, 4)

    def _load_buckets(self) -> dict[str, TokenBucket]:
        """Load data from persistent storage."""
        if not self._buckets_path.exists():
            return {}
        try:
            data = json.loads(self._buckets_path.read_text())
            return {
                k: TokenBucket(**{
                    key: val for key, val in v.items()
                    if key in TokenBucket.__dataclass_fields__
                })
                for k, v in data.items()
            }
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_buckets(self, buckets: dict[str, TokenBucket]) -> None:
        """Persist data to storage."""
        data = {k: asdict(v) for k, v in buckets.items()}
        self._buckets_path.write_text(json.dumps(data, indent=2))
