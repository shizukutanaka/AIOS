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
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from aictl.core.constants import (
    PREFIX_REUSE_FLUSH_EVERY,
    PREFIX_REUSE_MAX_AGE_SECONDS,
    PREFIX_REUSE_MAX_RECORDS,
)
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
        # Counts already written to the on-disk log, so a flush appends only
        # what is new. Tracked separately from the absolute counters so
        # in-process reads stay exact regardless of flush timing.
        self._flushed_lookups = 0
        self._flushed_hits = 0
        # Per-instance rather than a module global: a process-wide switch
        # flipped by the daemon leaked into every other tracker in the
        # process, so unrelated code started writing to the state directory.
        self._persist = False

    # Candidate prefix lengths to hash (must match best_endpoint)
    _PREFIX_LENS = [1024, 768, 512, 384, 256, 192, 128, 64, 32, 16]

    def record(self, endpoint: str, prompt: str) -> None:
        """Note that `endpoint` just served a request with this prompt prefix.

        Records ALL applicable prefix lengths so that partial matches work.
        """
        if not endpoint or not prompt:
            return
        now = time.time()
        with self._lock:
            history = self._endpoints.setdefault(endpoint, OrderedDict())
            for length in self._PREFIX_LENS:
                if length > len(prompt):
                    continue
                h = hashlib.sha256(
                    prompt[:length].encode("utf-8", errors="replace")
                ).hexdigest()[:16]
                history.pop(h, None)
                history[h] = now
            # Cap size
            while len(history) > self._capacity:
                history.popitem(last=False)

    def best_endpoint(
        self,
        prompt: str,
        endpoints: list[str],
    ) -> PrefixMatch | None:
        """Find the endpoint most likely to have this prompt's prefix cached.

        Returns None if no endpoint has any history. The overlap_score is
        based on the LONGEST matching prefix; we check 8 progressively
        shorter prefixes (each half the previous).
        """
        if not prompt or not endpoints:
            return None

        # Generate hashes for progressively shorter prefixes
        candidates: list[tuple[int, str]] = []
        prefix_lens = [PREFIX_HASH_LEN, 768, 512, 384, 256, 192, 128, 64, 32, 16]
        for length in prefix_lens:
            if length > len(prompt):
                continue
            piece = prompt[:length]
            candidates.append((length, hashlib.sha256(
                piece.encode("utf-8", errors="replace")
            ).hexdigest()[:16]))

        if not candidates:
            return None

        now = time.time()
        best: PrefixMatch | None = None

        with self._lock:
            for endpoint in endpoints:
                history = self._endpoints.get(endpoint)
                if not history:
                    continue

                for length, h in candidates:
                    if h in history:
                        last_seen = history[h]
                        age = now - last_seen
                        if age > self._ttl:
                            continue
                        overlap_ratio = length / PREFIX_HASH_LEN
                        freshness = max(0.0, 1.0 - age / self._ttl)
                        score = overlap_ratio * 0.7 + freshness * 0.3
                        if best is None or score > best.overlap_score:
                            best = PrefixMatch(
                                endpoint=endpoint,
                                overlap_score=score,
                                matched_prefix_len=length,
                            )

            # Counted inside the lock, and only for lookups that got far
            # enough to be answerable — early returns above are malformed
            # queries, not evidence about the workload.
            self._lookups += 1
            if best is not None:
                self._hits += 1
            due = (self._persist and PREFIX_REUSE_FLUSH_EVERY > 0
                   and self._lookups - self._flushed_lookups >= PREFIX_REUSE_FLUSH_EVERY)

        # Flushed outside the lock: it touches the filesystem, and routing
        # decisions must not wait on I/O.
        if due:
            self.flush_reuse()

        return best

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

        Deltas rather than absolute counts: appends under PIPE_BUF are atomic
        on POSIX, so concurrent processes can write to the same log without
        locking and a reader just sums. Absolute counts would need
        read-modify-write and would race.

        Best-effort — returns False and keeps the delta pending if the write
        fails. Measurement must never break routing.
        """
        # Reserve the delta and claim it in one atomic step. Computing the
        # delta, releasing the lock, then advancing the cursor lets two
        # concurrent flushes claim overlapping ranges and write the same
        # lookups twice — a real double-count observed under 6 threads.
        with self._lock:
            d_lookups = self._lookups - self._flushed_lookups
            d_hits = self._hits - self._flushed_hits
            if d_lookups <= 0:
                return False
            self._flushed_lookups += d_lookups
            self._flushed_hits += d_hits

        line = json.dumps({"lookups": d_lookups, "hits": d_hits,
                           "ts": int(time.time())},
                          separators=(",", ":")) + "\n"
        try:
            path = _reuse_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            # Un-claim so the counts are retried rather than lost.
            with self._lock:
                self._flushed_lookups -= d_lookups
                self._flushed_hits -= d_hits
            return False
        return True

    def reuse_rate(self) -> float | None:
        """Fraction of lookups that found a warm prefix, or None if unmeasured.

        None and 0.0 are deliberately different answers: None means nothing
        has been observed yet, 0.0 means reuse was observed to be absent.
        Callers act on them in opposite ways — `advise_kv_offload` falls back
        to a heuristic on None but vetoes offloading on a measured 0.0 — so
        collapsing the two would turn "no data" into "don't bother".
        """
        with self._lock:
            if self._lookups == 0:
                return None
            return self._hits / self._lookups

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
        with self._lock:
            self._endpoints.clear()
            self._lookups = 0
            self._hits = 0
            # The flush cursors must reset with the counters they index into.
            # Leaving them behind makes the next delta (lookups - flushed) go
            # negative, so a cleared tracker silently under-persists — or
            # persists nothing at all — until it exceeds the stale cursor.
            self._flushed_lookups = 0
            self._flushed_hits = 0

    def _hash_prefix(self, prompt: str) -> str:
        """Compute and return the hash."""
        piece = prompt[:PREFIX_HASH_LEN]
        return hashlib.sha256(
            piece.encode("utf-8", errors="replace")
        ).hexdigest()[:16]


def _reuse_log_path() -> Path:
    """Where the cross-process reuse log lives (mirrors core.perf's layout)."""
    base = resolve_state_dir()
    return Path(base) / "prefix_reuse.jsonl"


def persisted_reuse_rate() -> float | None:
    """Reuse rate accumulated across processes, or None if none recorded.

    Lets a short-lived CLI read a measurement produced by the long-lived
    daemon that actually served the traffic. Returns None (not 0.0) when the
    log is absent or unusable — see `PrefixRouteTracker.reuse_rate` for why
    that distinction is load-bearing.
    """
    try:
        path = _reuse_log_path()
        if not path.exists():
            return None
        cutoff = time.time() - PREFIX_REUSE_MAX_AGE_SECONDS
        lookups = hits = 0
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    # Valid JSON that isn't an object (a list, a bare number)
                    # would raise AttributeError on .get and escape the
                    # handler below, so reject by shape before reading it.
                    if not isinstance(rec, dict):
                        continue
                    # Stale traffic describes a workload that may no longer
                    # exist. Records predating the window are dropped, and
                    # untimestamped ones (written before ts existed) with them
                    # — "no data" is a safer answer than "old data".
                    if float(rec.get("ts", 0)) < cutoff:
                        continue
                    # A truncated final line from a crashed writer must not
                    # discard every valid record before it.
                    lookups += int(rec.get("lookups", 0))
                    hits += int(rec.get("hits", 0))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
        if lookups <= 0:
            return None
        return max(0.0, min(1.0, hits / lookups))
    except OSError:
        return None


def truncate_reuse_log() -> None:
    """Collapse the log to a single summary record once it grows past bound.

    Keeps the accumulated totals rather than dropping history outright, so
    trimming does not distort the rate.
    """
    try:
        path = _reuse_log_path()
        if not path.exists():
            return
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) <= PREFIX_REUSE_MAX_RECORDS:
            return
        lookups = hits = 0
        for line in lines:
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    continue
                lookups += int(rec.get("lookups", 0))
                hits += int(rec.get("hits", 0))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            # Stamped now: the summary is only as trustworthy as the moment it
            # was written, and an unstamped record would be dropped as stale.
            fh.write(json.dumps({"lookups": lookups, "hits": hits,
                                 "ts": int(time.time())},
                                separators=(",", ":")) + "\n")
        tmp.replace(path)
    except OSError:
        pass


# Process-local singleton
_DEFAULT_TRACKER = PrefixRouteTracker()


def get_default_tracker() -> PrefixRouteTracker:
    """Return the global default PrefixRouteTracker instance."""
    return _DEFAULT_TRACKER
