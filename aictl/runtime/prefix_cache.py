"""Prefix cache analytics: track KV cache sharing efficiency.

LLM inference engines reuse KV cache for shared prefixes:
  - System prompts shared across all requests
  - RAG context shared within a session
  - Multi-turn conversations share previous turns

This module tracks:
  - Cache hit rate (from engine metrics)
  - Estimated compute savings (avoided prefill tokens)
  - Sharing efficiency across tenants
  - Cost savings from prefix caching

Based on:
  - vLLM automatic prefix caching (V1 engine default)
  - SGLang RadixAttention (radix tree for fine-grained reuse)
  - llm-d v0.5 cache-aware routing
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheStats:
    """KV cache statistics from an inference engine."""
    engine: str = ""
    endpoint: str = ""
    timestamp: float = 0.0
    # Hit rates
    hit_rate: float = 0.0          # 0.0-1.0
    prefix_hit_tokens: int = 0     # Tokens served from cache
    prefix_miss_tokens: int = 0    # Tokens that required compute
    # Cache state
    kv_cache_usage: float = 0.0    # 0.0-1.0
    kv_cache_blocks_used: int = 0
    kv_cache_blocks_total: int = 0
    # Requests
    active_requests: int = 0
    waiting_requests: int = 0
    # Estimated savings
    saved_prefill_ms: float = 0.0
    saved_compute_cost: float = 0.0


def scrape_cache_stats(engine: str, endpoint: str) -> CacheStats:
    """Scrape KV cache metrics from an inference engine."""
    stats = CacheStats(engine=engine, endpoint=endpoint, timestamp=time.time())

    try:
        url = f"{endpoint.rstrip('/')}/metrics"
        with urllib.request.urlopen(url, timeout=5) as resp:
            metrics = resp.read().decode()
    except Exception:
        return stats

    for line in metrics.splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name, value = parts[0], parts[1]

        try:
            val = float(value)
        except ValueError:
            continue

        # vLLM metrics
        if name == "vllm:kv_cache_usage_perc":
            stats.kv_cache_usage = val
        elif name == "vllm:num_requests_running":
            stats.active_requests = int(val)
        elif name == "vllm:num_requests_waiting":
            stats.waiting_requests = int(val)
        elif name == "vllm:prefix_cache_hit_rate":
            stats.hit_rate = val
        elif name == "vllm:prefix_cache_hit_tokens_total":
            stats.prefix_hit_tokens = int(val)
        elif name == "vllm:prefix_cache_miss_tokens_total":
            stats.prefix_miss_tokens = int(val)

        # SGLang metrics
        elif name == "sglang_cache_hit_rate":
            stats.hit_rate = val
        elif name == "sglang_cache_total_tokens":
            stats.prefix_hit_tokens = int(val)

    # Estimate savings
    if stats.prefix_hit_tokens > 0:
        # Rough: 0.1ms per token prefill on H100
        stats.saved_prefill_ms = stats.prefix_hit_tokens * 0.1
        # Rough: $2.50/hr H100 → $0.000694/sec → $0.0000694/100ms
        stats.saved_compute_cost = (stats.saved_prefill_ms / 1000) * 0.000694

    return stats


def analyze_cache_efficiency(
    engines: dict[str, str],
) -> dict[str, Any]:
    """Analyze prefix cache efficiency across all engines."""
    results: list[CacheStats] = []
    for engine, endpoint in engines.items():
        stats = scrape_cache_stats(engine, endpoint)
        results.append(stats)

    total_hit = sum(s.prefix_hit_tokens for s in results)
    total_miss = sum(s.prefix_miss_tokens for s in results)
    total_tokens = total_hit + total_miss
    overall_hit_rate = total_hit / total_tokens if total_tokens > 0 else 0.0

    total_saved_ms = sum(s.saved_prefill_ms for s in results)
    total_saved_cost = sum(s.saved_compute_cost for s in results)

    avg_kv_usage = (sum(s.kv_cache_usage for s in results) / len(results)
                    if results else 0.0)

    return {
        "overall_hit_rate": round(overall_hit_rate, 4),
        "total_hit_tokens": total_hit,
        "total_miss_tokens": total_miss,
        "avg_kv_cache_usage": round(avg_kv_usage, 4),
        "estimated_saved_prefill_ms": round(total_saved_ms, 1),
        "estimated_saved_cost_usd": round(total_saved_cost, 6),
        "engines": len(results),
        "recommendation": _cache_recommendation(overall_hit_rate, avg_kv_usage),
    }


def _cache_recommendation(hit_rate: float, kv_usage: float) -> str:
    """Generate recommendation based on cache stats."""
    if hit_rate >= 0.8:
        return "Excellent cache efficiency — prefix sharing is highly effective"
    elif hit_rate >= 0.5:
        if kv_usage > 0.9:
            return "Good hit rate but KV cache near capacity — consider increasing gpu-memory-utilization"
        return "Good cache efficiency — consider grouping similar prompts to improve further"
    elif hit_rate >= 0.2:
        return "Moderate cache efficiency — enable prefix caching (--enable-prefix-caching) and use consistent system prompts"
    elif hit_rate > 0:
        return "Low cache efficiency — workload has few shared prefixes; consider standardizing system prompts"
    return "No cache data available — enable prefix caching on your inference engine"
