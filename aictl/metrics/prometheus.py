"""Prometheus /metrics endpoint for aiosd.

Exposes AI OS metrics in Prometheus text format so that:
  1. OTel Collector can scrape aiosd directly
  2. Prometheus can scrape without OTel
  3. Grafana Agent/Alloy can scrape

Metric naming follows OTel GenAI SemConv where applicable.
"""

from __future__ import annotations

import time
from typing import Any

from aictl.metrics.slo import read_psi
from aictl.runtime.adapters import get_adapter
from aictl.core.config import load_config
from aictl.core.state import StateStore


def generate_metrics_text(store: StateStore) -> str:
    """Generate Prometheus text format metrics."""
    lines: list[str] = []
    int(time.time() * 1000)

    config = load_config(store.dir)
    node = store.load_node()
    stacks = store.load_stacks()

    # Node info
    _gauge(lines, "aios_node_info", "Node information",
           1, {"node_id": node.node_id, "profile": node.profile,
               "hostname": node.hostname, "version": node.version})
    _gauge(lines, "aios_node_gpu_count", "Number of GPUs", node.gpu_count)
    _gauge(lines, "aios_node_vram_total_mb", "Total VRAM in MB", node.vram_total_mb)
    _gauge(lines, "aios_node_ram_total_mb", "Total RAM in MB", node.ram_total_mb)
    _gauge(lines, "aios_stacks_active", "Active stacks", len(stacks))

    # PSI
    psi = read_psi()
    _gauge(lines, "aios_psi_memory_some_avg10", "Memory pressure some avg10 percent", psi.memory_some_avg10)
    _gauge(lines, "aios_psi_memory_full_avg10", "Memory pressure full avg10 percent", psi.memory_full_avg10)
    _gauge(lines, "aios_psi_memory_some_avg60", "Memory pressure some avg60 percent", psi.memory_some_avg60)
    _gauge(lines, "aios_psi_cpu_some_avg10", "CPU pressure some avg10 percent", psi.cpu_some_avg10)
    _gauge(lines, "aios_psi_io_some_avg10", "IO pressure some avg10 percent", psi.io_some_avg10)

    # Engine metrics
    endpoints = config.engines.to_dict()
    for engine_name, endpoint in endpoints.items():
        adapter = get_adapter(engine_name, endpoint)
        if not adapter:
            continue

        try:
            health = adapter.health()
            reachable = 1 if health.reachable else 0
            _gauge(lines, "aios_engine_reachable", "Engine reachable",
                   reachable, {"engine": engine_name})
            _gauge(lines, "aios_engine_health_latency_ms", "Health check latency ms",
                   round(health.latency_ms, 1), {"engine": engine_name})

            if health.reachable:
                ready = 1 if health.status == "READY" else 0
                _gauge(lines, "aios_engine_ready", "Engine ready state",
                       ready, {"engine": engine_name})

                metrics = adapter.scrape_metrics()
                labels = {"engine": engine_name}

                _gauge(lines, "gen_ai_server_time_to_first_token_p95_ms",
                       "TTFT p95 in milliseconds", metrics.ttft_ms_p95, labels)
                _gauge(lines, "gen_ai_server_time_per_output_token_p95_ms",
                       "ITL p95 in milliseconds", metrics.itl_ms_p95, labels)
                _gauge(lines, "aios_inference_throughput_tokens_per_sec",
                       "Token generation throughput", metrics.tokens_per_sec, labels)
                _gauge(lines, "aios_inference_queue_depth",
                       "Pending requests", metrics.queue_depth, labels)
                _gauge(lines, "aios_inference_active_requests",
                       "Active requests", metrics.active_requests, labels)
                _gauge(lines, "aios_inference_error_rate",
                       "Error rate", metrics.error_rate, labels)
                _gauge(lines, "aios_inference_kv_cache_utilization",
                       "KV cache utilization ratio", metrics.kv_cache_utilization, labels)
                _gauge(lines, "aios_inference_vram_used_mb",
                       "VRAM used in MB", metrics.vram_used_mb, labels)

        except Exception:
            _gauge(lines, "aios_engine_reachable", "Engine reachable",
                   0, {"engine": engine_name})

    # Models
    models = store.list_models()
    _gauge(lines, "aios_models_registered", "Number of registered models", len(models))

    # ── Value-prop metrics ────────────────────────────────────────────
    # The headline claims (cache savings, cost avoided, tokens metered) that
    # peers (LiteLLM/Portkey/Helicone) expose as counters — emitted here so
    # dashboards can *prove* the ROI instead of asserting it. All best-effort:
    # a failure in any block must not break the /metrics endpoint.
    _emit_value_prop_metrics(lines)

    return "\n".join(lines) + "\n"


def _emit_value_prop_metrics(lines: list[str]) -> None:
    """Emit cache-savings, cost-avoided, and metering counters (best-effort)."""
    from aictl.core.constants import PRICE_PER_MILLION_INPUT, PRICE_PER_MILLION_OUTPUT
    # Cache responses avoid both prompt re-send and generation; blend the two.
    blended_price_per_million = (PRICE_PER_MILLION_INPUT + PRICE_PER_MILLION_OUTPUT) / 2

    # Semantic cache: lifetime savings (the core "30-50% cost cut" claim).
    try:
        from aictl.core.sem_cache import get_default_cache
        stats = get_default_cache().stats()
        tokens_saved = int(stats.get("lifetime_tokens_saved", 0) or 0)
        cost_saved_usd = tokens_saved / 1_000_000 * blended_price_per_million
        _gauge(lines, "aios_cache_entries",
               "Semantic cache entries", int(stats.get("entries", 0) or 0))
        _counter(lines, "aios_cache_hits_total",
                 "Lifetime semantic cache hits", int(stats.get("lifetime_hits", 0) or 0))
        _counter(lines, "aios_cache_tokens_saved_total",
                 "Lifetime tokens served from cache (not re-inferred)", tokens_saved)
        _counter(lines, "aios_cache_cost_saved_usd_total",
                 "Estimated USD saved by cache hits", round(cost_saved_usd, 6))
        _gauge(lines, "aios_cache_hit_rate",
               "Session cache hit rate (0-1)", stats.get("session_hit_rate", 0.0))
    except Exception:
        pass  # cache DB may be absent; skip silently

    # Metering: tokens and cost attributed across tenants/keys.
    try:
        from aictl.core.metering import TokenMeter
        buckets = TokenMeter().list_usage()
        total_tokens = sum(b.total_tokens for b in buckets)
        total_prompt = sum(b.prompt_tokens for b in buckets)
        total_completion = sum(b.completion_tokens for b in buckets)
        metered_cost = (total_prompt / 1_000_000 * PRICE_PER_MILLION_INPUT
                        + total_completion / 1_000_000 * PRICE_PER_MILLION_OUTPUT)
        _counter(lines, "aios_tokens_metered_total",
                 "Total tokens metered across all entities", total_tokens)
        _counter(lines, "aios_cost_metered_usd_total",
                 "Estimated USD cost of metered tokens", round(metered_cost, 6))
        _gauge(lines, "aios_metered_entities",
               "Number of metered tenants/keys", len(buckets))
    except Exception:
        pass  # metering store may be empty; skip silently


def _escape_label(v: Any) -> str:
    """Escape a Prometheus label value (\\, ", and newline) per the text format."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _gauge(lines: list[str], name: str, help_text: str,
           value: Any, labels: dict[str, str] | None = None) -> None:
    """Execute gauge."""
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    if labels:
        label_str = ",".join(f'{k}="{_escape_label(v)}"' for k, v in labels.items())
        lines.append(f"{name}{{{label_str}}} {value}")
    else:
        lines.append(f"{name} {value}")


def _counter(lines: list[str], name: str, help_text: str,
             value: Any, labels: dict[str, str] | None = None) -> None:
    """Emit a Prometheus counter (monotonic; convention: _total suffix)."""
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} counter")
    if labels:
        label_str = ",".join(f'{k}="{_escape_label(v)}"' for k, v in labels.items())
        lines.append(f"{name}{{{label_str}}} {value}")
    else:
        lines.append(f"{name} {value}")
