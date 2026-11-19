"""Prefix-cache aware ROUTING — KV cache locality for routing decisions.

Distinct from prefix_cache.py (which is analytics).

SGLang's RadixAttention gives 6.4x throughput on prefix-heavy workloads
(RAG, multi-turn chat, system prompts). The trick: when a new request
arrives whose prompt prefix overlaps something already in a server's
KV cache, route it to that server.

We do this without changing inference engines. We track which prefix
hashes are 'warm' on which engine endpoint, and bias routing toward
the warmest match.

Design:
  - Each request's first 1024 chars get hashed (prefix fingerprint)
  - For each engine endpoint, we track a TTL'd LRU of recent prefix hashes
  - On routing, pick the endpoint with the most matching prefix hashes
  - Decay: entries expire after 5 minutes (typical KV cache lifetime)
  - Storage: in-memory (per-process); thread-safe
"""

from __future__ import annotations

from typing import Any

import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from aictl.core.atomicio import atomic_write_text
from aictl.core.constants import (
    PREFIX_REUSE_COMPACTION_SECONDS,
    PREFIX_REUSE_FLUSH_EVERY,
    PREFIX_REUSE_MAX_AGE_SECONDS,
    PREFIX_REUSE_MAX_RECORDS,
)
from aictl.core.filelock import file_lock
from aictl.core.state import resolve_state_dir


# How long a prefix is assumed warm in a server's KV cache
PREFIX_TTL_SECONDS = 300  # 5 minutes
# How many distinct prefixes we track per endpoint
PREFIX_LRU_CAPACITY = 1000
# Length of the prefix we hash (chars, not tokens)
PREFIX_HASH_LEN = 1024


@dataclass(frozen=True)
class PrefixMatch:
    """Result of looking up a prompt's prefix locality."""
    endpoint: str
    overlap_score: float  # 0.0 to 1.0; higher = more likely cache hit
    matched_prefix_len: int  # how many chars of overlap


class PrefixRouteTracker:
    """Thread-safe per-endpoint TTL'd prefix history for routing.

    For each endpoint we keep an OrderedDict of prefix_hash → last_seen_time,
    bounded to PREFIX_LRU_CAPACITY entries.
    """

    def __init__(self, ttl_seconds: int = PREFIX_TTL_SECONDS,
                 capacity: int = PREFIX_LRU_CAPACITY):
        """Initialize the instance with provided arguments."""
        self._ttl = ttl_seconds
        self._capacity = capacity
        self._endpoints: dict[str, OrderedDict[str, float]] = {}
        self._lock = threading.RLock()
        # Lookup accounting. Routing already knows, per request, whether a
        # warm prefix existed — it just threw that away. Keeping it turns the
        # router into a free measurement of how prefix-heavy the workload
        # actually is, which is the one thing that decides whether extending
        # the KV cache (see runtime/kv_offload.py) can pay off at all.
        self._lookups = 0
        self._hits = 0
        self._scoped_counts: dict[str, tuple[int, int]] = {}
        self._pending: dict[str | None, tuple[int, int]] = {}
        self._pending_lookups = 0
        self._flush_lock = threading.Lock()
        # Per-instance rather than a module global: a process-wide switch
        # flipped by the daemon leaked into every other tracker in the
        # process, so unrelated code started writing to the state directory.
        self._persist = False

    _PREFIX_LENS = [PREFIX_HASH_LEN, 768, 512, 384, 256, 192, 128, 64, 32, 16]

    def _prefix_hashes(self, prompt: str, model: str) -> list[tuple[int, str]]:
        if len(prompt) < self._PREFIX_LENS[-1]:
            return []
        model_key = hashlib.sha256(model.encode("utf-8", errors="replace")).digest()
        return [
            (length, hashlib.sha256(
                model_key + prompt[:length].encode("utf-8", errors="replace")
            ).hexdigest()[:16])
            for length in self._PREFIX_LENS if length <= len(prompt)
        ]

    def record(self, endpoint: str, prompt: str, *, model: str = "") -> None:
        """Note that `endpoint` just served a request with this prompt prefix.

        Records ALL applicable prefix lengths so that partial matches work.
        """
        if not endpoint or not prompt:
            return
        candidates = self._prefix_hashes(prompt, model)
        if not candidates:
            return
        now = time.time()
        with self._lock:
            self._record_locked(endpoint, candidates, now)

    def _record_locked(
        self, endpoint: str, candidates: list[tuple[int, str]], now: float,
    ) -> None:
        history = self._endpoints.setdefault(endpoint, OrderedDict())
        for _, h in candidates:
            history.pop(h, None)
            history[h] = now
        while len(history) > self._capacity:
            history.popitem(last=False)

    def observe(self, endpoint: str, prompt: str, *, model: str = "") -> None:
        """Count one served request against the selected endpoint before warming it."""
        if not endpoint or not prompt:
            return
        candidates = self._prefix_hashes(prompt, model)
        if not candidates:
            return
        now = time.time()
        with self._lock:
            match = self._best_match_locked(candidates, [endpoint], now)
            due = self._count_lookup_locked(match, _endpoint_id(endpoint))
            self._record_locked(endpoint, candidates, now)
        if due:
            self.flush_reuse()

    def best_endpoint(
        self,
        prompt: str,
        endpoints: list[str],
        *,
        model: str = "",
        count: bool = True,
    ) -> PrefixMatch | None:
        """Find the endpoint most likely to have this prompt's prefix cached.

        Returns None if no endpoint has a warm prefix for this model.
        The overlap_score uses the length and freshness of matching prefixes.
        Use count=False when probing candidates before a request is served.
        """
        if not prompt or not endpoints:
            return None

        candidates = self._prefix_hashes(prompt, model)
        if not candidates:
            return None

        now = time.time()
        with self._lock:
            best = self._best_match_locked(candidates, endpoints, now)
            due = count and self._count_lookup_locked(best)

        # Flushed outside the lock: it touches the filesystem, and routing
        # decisions must not wait on I/O.
        if due:
            self.flush_reuse()

        return best

    def _best_match_locked(
        self, candidates: list[tuple[int, str]], endpoints: list[str], now: float,
    ) -> PrefixMatch | None:
        best: PrefixMatch | None = None
        for endpoint in endpoints:
            history = self._endpoints.get(endpoint)
            if not history:
                continue
            for length, h in candidates:
                last_seen = history.get(h)
                if last_seen is None:
                    continue
                age = now - last_seen
                if age > self._ttl:
                    continue
                overlap_ratio = length / PREFIX_HASH_LEN
                freshness = max(0.0, 1.0 - age / self._ttl)
                score = overlap_ratio * 0.7 + freshness * 0.3
                if best is None or score > best.overlap_score:
                    best = PrefixMatch(
                        endpoint=endpoint, overlap_score=score, matched_prefix_len=length,
                    )
        return best

    def _count_lookup_locked(
        self, match: PrefixMatch | None, endpoint_id: str | None = None,
    ) -> bool:
        hit = int(match is not None)
        self._lookups += 1
        self._hits += hit
        if endpoint_id is not None:
            lookups, hits = self._scoped_counts.get(endpoint_id, (0, 0))
            self._scoped_counts[endpoint_id] = lookups + 1, hits + hit
        lookups, hits = self._pending.get(endpoint_id, (0, 0))
        self._pending[endpoint_id] = lookups + 1, hits + hit
        self._pending_lookups += 1
        return (self._persist and PREFIX_REUSE_FLUSH_EVERY > 0
                and self._pending_lookups >= PREFIX_REUSE_FLUSH_EVERY)

    def enable_persistence(self, enabled: bool = True) -> None:
        """Opt this tracker into periodically persisting its reuse counts.

        Off by default. Writing to disk from the routing path is only
        justified in a long-lived process whose measurements someone will
        later read — the daemon. On by default, every short-lived CLI run and
        every test would drop files into the user's state directory for a
        measurement nothing consumes.
        """
        with self._lock:
            self._persist = enabled

    def persistence_enabled(self) -> bool:
        """Whether this tracker auto-flushes its reuse counts."""
        with self._lock:
            return self._persist

    def flush_reuse(self) -> bool:
        """Append this tracker's unflushed hit/miss delta to the reuse log.

        Best-effort — returns False and keeps the delta pending if the write
        fails. Measurement must never break routing.
        """
        with self._flush_lock:
            with self._lock:
                if not self._pending:
                    return False
                pending, pending_lookups = self._pending, self._pending_lookups
                self._pending, self._pending_lookups = {}, 0
            ts = int(time.time())
            try:
                path = _reuse_log_path()
                lines = "".join(_reuse_line(scope, lookups, hits, ts)
                                for scope, (lookups, hits) in pending.items())
                with file_lock(path), open(path, "a+b", buffering=0) as fh:
                    end = fh.seek(0, 2)
                    if end:
                        fh.seek(-1, 2)
                        if fh.read(1) != b"\n":
                            lines = "\n" + lines
                    data = lines.encode("utf-8")
                    try:
                        if fh.write(data) != len(data):
                            raise OSError("short prefix reuse log write")
                    except OSError:
                        fh.truncate(end)
                        raise
            except OSError:
                with self._lock:
                    for scope, (lookups, hits) in pending.items():
                        new_lookups, new_hits = self._pending.get(scope, (0, 0))
                        self._pending[scope] = lookups + new_lookups, hits + new_hits
                    self._pending_lookups += pending_lookups
                return False
            return True

    def reuse_rate(self, endpoint: str | None = None) -> float | None:
        """Fraction of lookups that found a warm prefix, or None if unmeasured.

        None and 0.0 are deliberately different answers: None means nothing
        has been observed yet, 0.0 means reuse was observed to be absent.
        Callers act on them in opposite ways — `advise_kv_offload` falls back
        to a heuristic on None but vetoes offloading on a measured 0.0 — so
        collapsing the two would turn "no data" into "don't bother".
        With an endpoint, only requests actually observed there are counted.
        """
        with self._lock:
            if endpoint is None:
                lookups, hits = self._lookups, self._hits
            else:
                lookups, hits = self._scoped_counts.get(_endpoint_id(endpoint), (0, 0))
        return hits / lookups if lookups else None

    def stats(self) -> dict[str, Any]:
        """For debugging."""
        with self._lock:
            now = time.time()
            return {
                "endpoints": list(self._endpoints.keys()),
                "lookups": self._lookups,
                "hits": self._hits,
                "reuse_rate": (self._hits / self._lookups) if self._lookups else None,
                "totals": {
                    ep: {
                        "tracked_prefixes": len(history),
                        "live_prefixes": sum(
                            1 for ts in history.values()
                            if now - ts <= self._ttl
                        ),
                    }
                    for ep, history in self._endpoints.items()
                },
            }

    def clear(self) -> None:
        """Clear stored data."""
        with self._flush_lock, self._lock:
            self._endpoints.clear()
            self._lookups = 0
            self._hits = 0
            self._scoped_counts.clear()
            self._pending.clear()
            self._pending_lookups = 0


def _reuse_log_path() -> Path:
    """Where the cross-process reuse log lives (mirrors core.perf's layout)."""
    base = resolve_state_dir()
    return Path(base) / "prefix_reuse.jsonl"


def _endpoint_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.rstrip("/").encode("utf-8", errors="replace")).hexdigest()


def _reuse_line(scope: str | None, lookups: int, hits: int, ts: float) -> str:
    record: dict[str, str | int | float] = {"lookups": lookups, "hits": hits, "ts": ts}
    if scope is not None:
        record["endpoint_id"] = scope
    return json.dumps(record, separators=(",", ":")) + "\n"


def _read_reuse_line(line: str, cutoff: float) -> tuple[str | None, int, int, float] | None:
    try:
        record = json.loads(line)
        if not isinstance(record, dict):
            return None
        ts = float(record.get("ts", 0))
        if not math.isfinite(ts) or ts < cutoff:
            return None
        scope = record.get("endpoint_id")
        if scope is not None and not isinstance(scope, str):
            return None
        lookups = int(record.get("lookups", 0))
        hits = int(record.get("hits", 0))
        if lookups > 0:
            return scope, lookups, hits, ts
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def persisted_reuse_rate(endpoint: str | None = None) -> float | None:
    """Reuse rate accumulated across processes, or None if none recorded.

    Lets a short-lived CLI read a measurement produced by the long-lived
    daemon that actually served the traffic. Returns None (not 0.0) when the
    log is absent or unusable — see `PrefixRouteTracker.reuse_rate` for why
    that distinction is load-bearing.
    Legacy records without identity contribute only to the unscoped rate.
    """
    try:
        path = _reuse_log_path()
        if not path.exists():
            return None
        cutoff = time.time() - PREFIX_REUSE_MAX_AGE_SECONDS
        lookups = hits = 0
        scope = _endpoint_id(endpoint) if endpoint is not None else None
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                record = _read_reuse_line(line, cutoff)
                if record is not None and (scope is None or record[0] == scope):
                    lookups += record[1]
                    hits += record[2]
        if lookups <= 0:
            return None
        return max(0.0, min(1.0, hits / lookups))
    except OSError:
        return None


def truncate_reuse_log() -> None:
    """Compact fresh records by endpoint and age once the log exceeds its bound."""
    try:
        path = _reuse_log_path()
        if not path.exists():
            return
        with file_lock(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            if len(lines) <= PREFIX_REUSE_MAX_RECORDS:
                return
            now = time.time()
            cutoff = now - PREFIX_REUSE_MAX_AGE_SECONDS
            totals: dict[tuple[str | None, int], tuple[int, int, float]] = {}
            for line in lines:
                record = _read_reuse_line(line, cutoff)
                if record is None:
                    continue
                scope, lookups, hits, ts = record
                bucket = (scope, math.floor(ts / PREFIX_REUSE_COMPACTION_SECONDS))
                prev_lookups, prev_hits, oldest_ts = totals.get(bucket, (0, 0, ts))
                totals[bucket] = prev_lookups + lookups, prev_hits + hits, min(ts, oldest_ts)
            atomic_write_text(path, "".join(
                _reuse_line(scope, lookups, hits, ts)
                for (scope, _), (lookups, hits, ts) in totals.items()
            ))
    except OSError:
        pass


# Process-local singleton
_DEFAULT_TRACKER = PrefixRouteTracker()


def get_default_tracker() -> PrefixRouteTracker:
    """Return the global default PrefixRouteTracker instance."""
    return _DEFAULT_TRACKER
