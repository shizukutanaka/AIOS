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
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


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

        return best

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

    def _hash_prefix(self, prompt: str) -> str:
        """Compute and return the hash."""
        piece = prompt[:PREFIX_HASH_LEN]
        return hashlib.sha256(
            piece.encode("utf-8", errors="replace")
        ).hexdigest()[:16]


# Process-local singleton
_DEFAULT_TRACKER = PrefixRouteTracker()


def get_default_tracker() -> PrefixRouteTracker:
    """Return the global default PrefixRouteTracker instance."""
    return _DEFAULT_TRACKER
