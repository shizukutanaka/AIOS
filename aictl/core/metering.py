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


@dataclass(frozen=True)
class WindowBucket:
    """Windowed usage for one entity, shaped like TokenBucket on purpose.

    Both fairness consumers substitute this for cumulative usage without
    changing their own logic: `fair_scheduler.weighted_service()` reads
    `.prompt_tokens` / `.completion_tokens`, and `fairness.compute_fairness()`
    reads `.entity_id`, `.entity_type` and `.total_tokens`. `entity_type` is
    carried for that second consumer — the first version omitted it, which
    made the window usable by the gate and not by the report.

    The metering log records no entity_type, so it defaults to the same
    "apikey" that `TokenMeter.record()` defaults to rather than being invented
    per event.
    """
    entity_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    entity_type: str = "apikey"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class WindowedUsage:
    """The result of a windowed read, including whether it is trustworthy."""
    buckets: dict[str, WindowBucket]
    window_seconds: float
    complete: bool
    events_scanned: int

    def as_list(self) -> list[WindowBucket]:
        return list(self.buckets.values())

    def to_dict(self) -> dict[str, object]:
        return {"window_seconds": self.window_seconds,
                "complete": self.complete,
                "events_scanned": self.events_scanned,
                "entities": len(self.buckets)}


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
            from aictl.core.state import resolve_state_dir
            state_dir = resolve_state_dir()
        self.dir = state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._buckets_path = self.dir / "metering.json"
        self._log_path = self.dir / "metering_log.jsonl"

    def record(self, entity_id: str, model: str,
               prompt_tokens: int, completion_tokens: int,
               latency_ms: float = 0.0,
               entity_type: str = "apikey") -> bool:
        """Record token usage. Returns False if quota exceeded."""
        prompt_tokens = max(0, prompt_tokens)
        completion_tokens = max(0, completion_tokens)
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

    def window_usage(self, window_seconds: float, *,
                     now: float | None = None,
                     max_events: int | None = None,
                     tail_bytes: int | None = None) -> "WindowedUsage":
        """Per-entity prompt/completion tokens within a recent time window.

        metering_log.jsonl was written by `record()` and read by nothing — a
        write-only, unbounded log. It is the only place carrying a per-event
        prompt/completion split with a real timestamp, so it is what a rolling
        window has to be built from: `TokenBucket.tokens_today` and friends
        reset on calendar boundaries rather than sliding, and carry no split,
        so a windowed weighted service is simply not expressible from them.

        Read from the tail, newest first, under two caps so the cost cannot
        grow with the log. If either cap is hit before the window is covered,
        the result is marked `complete=False` — the caller then has partial
        data and knows it, rather than a confident-looking undercount. Callers
        that throttle must fall back to cumulative in that case: deferring a
        tenant on a window you failed to measure is worse than not deferring.
        """
        from aictl.core.constants import (
            METERING_WINDOW_MAX_EVENTS,
            METERING_WINDOW_TAIL_BYTES,
        )

        cap_events = METERING_WINDOW_MAX_EVENTS if max_events is None else max_events
        cap_bytes = METERING_WINDOW_TAIL_BYTES if tail_bytes is None else tail_bytes
        cutoff = (time.time() if now is None else now) - max(0.0, window_seconds)

        totals: dict[str, list[int]] = {}
        scanned = 0
        complete = True
        try:
            size = self._log_path.stat().st_size
            with open(self._log_path, "rb") as handle:
                start = max(0, size - cap_bytes)
                handle.seek(start)
                chunk = handle.read()
            lines = chunk.split(b"\n")
            if start > 0 and lines:
                lines.pop(0)          # a partial line the seek cut in half
            truncated = start > 0
            reached_start = True
            for raw in reversed(lines):
                if not raw.strip():
                    continue
                if scanned >= cap_events:
                    complete = False
                    reached_start = False
                    break
                try:
                    event = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue          # a torn line; skip it, do not fail the read
                if not isinstance(event, dict):
                    continue
                scanned += 1
                if float(event.get("timestamp", 0.0)) < cutoff:
                    reached_start = True
                    break
                entity = str(event.get("entity_id", ""))
                if not entity:
                    continue
                bucket = totals.setdefault(entity, [0, 0])
                bucket[0] += int(event.get("prompt_tokens", 0) or 0)
                bucket[1] += int(event.get("completion_tokens", 0) or 0)
            else:
                # Ran out of lines without crossing the cutoff. That only
                # covers the window if we read the whole file.
                reached_start = not truncated
            if not reached_start:
                complete = False
        except FileNotFoundError:
            # No log yet: an empty window is the truthful answer, and it is
            # complete — there genuinely is no usage in it.
            return WindowedUsage({}, window_seconds, True, 0)
        except OSError:
            return WindowedUsage({}, window_seconds, False, 0)

        return WindowedUsage(
            {name: WindowBucket(name, counts[0], counts[1])
             for name, counts in totals.items()},
            window_seconds, complete, scanned)

    def get_usage(self, entity_id: str) -> TokenBucket | None:
        """Get usage."""
        buckets = self._load_buckets()
        return buckets.get(entity_id)

    def set_quota(self, entity_id: str, *,
                  per_day: int | None = None, per_month: int | None = None,
                  per_minute: int | None = None) -> None:
        """Set token quotas for an entity. Pass 0 to reset a quota to unlimited."""
        buckets = self._load_buckets()
        bucket = buckets.get(entity_id)
        if bucket is None:
            bucket = TokenBucket(entity_id=entity_id)
            buckets[entity_id] = bucket
        if per_day is not None:
            bucket.quota_tokens_per_day = per_day
        if per_month is not None:
            bucket.quota_tokens_per_month = per_month
        if per_minute is not None:
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
            # A list/scalar-rooted file parses cleanly but `data.items()` (or a
            # non-dict per-entity value's `v.items()`) raises AttributeError —
            # uncaught, surfaced as "report a bug" for a corrupt store. Same V7
            # class as config.py/batch.py/etc.
            if not isinstance(data, dict):
                return {}
            return {
                k: TokenBucket(**{
                    key: val for key, val in v.items()
                    if key in TokenBucket.__dataclass_fields__
                })
                for k, v in data.items() if isinstance(v, dict)
            }
        except (json.JSONDecodeError, OSError, AttributeError, TypeError):
            return {}

    def _save_buckets(self, buckets: dict[str, TokenBucket]) -> None:
        """Persist data to storage."""
        from aictl.core.atomicio import atomic_write_text
        data = {k: asdict(v) for k, v in buckets.items()}
        # Atomic: token buckets are billing state — a crash mid-write must not
        # corrupt them.
        atomic_write_text(self._buckets_path, json.dumps(data, indent=2))
