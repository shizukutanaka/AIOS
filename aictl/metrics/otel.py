"""OpenTelemetry metrics exporter for AI workload observability.

Exports AI-specific metrics to an OTel Collector endpoint:
  - Inference latency (TTFT, ITL, TPOT)
  - Throughput (tokens/sec)
  - Queue depth and active requests
  - VRAM utilization
  - KV cache utilization
  - Error rate
  - PSI memory/CPU/IO pressure
  - Model state transitions

Uses OTLP/HTTP JSON protocol — no heavy SDK dependency for MVP.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from aictl.metrics.slo import InferenceMetrics, SystemPressure


OTEL_DEFAULT_ENDPOINT = "http://localhost:4318/v1/metrics"


@dataclass
class OTelMetricPoint:
    name: str
    description: str
    unit: str
    value: float
    attributes: dict[str, str] = field(default_factory=dict)
    timestamp_ns: int = 0


def build_metric_payload(
    metrics: InferenceMetrics,
    pressure: SystemPressure,
    node_id: str = "",
    profile: str = "",
) -> dict[str, Any]:
    """Build an OTLP/HTTP JSON payload from current metrics."""
    now_ns = int(time.time() * 1_000_000_000)

    resource_attrs = [
        {"key": "service.name", "value": {"stringValue": "aios"}},
        {"key": "service.version", "value": {"stringValue": "0.4.0"}},
    ]
    if node_id:
        resource_attrs.append({"key": "aios.node_id", "value": {"stringValue": node_id}})
    if profile:
        resource_attrs.append({"key": "aios.profile", "value": {"stringValue": profile}})
    resource = {"attributes": resource_attrs}

    points: list[dict[str, Any]] = []

    # Inference metrics (aligned with OTel GenAI SemConv experimental)
    gauge_metrics = [
        ("gen_ai.server.time_to_first_token", "Time to first token p95", "ms", metrics.ttft_ms_p95),
        ("gen_ai.server.time_to_first_token.p50", "Time to first token p50", "ms", metrics.ttft_ms_p50),
        ("gen_ai.server.time_per_output_token", "Inter-token latency p95", "ms", metrics.itl_ms_p95),
        ("aios.inference.tpot", "Time per output token", "ms", metrics.tpot_ms),
        ("aios.inference.throughput", "Token generation throughput", "tokens/s", metrics.tokens_per_sec),
        ("aios.inference.goodput_ratio", "Fraction of requests meeting latency SLOs", "1", metrics.goodput_ratio),
        ("aios.inference.queue_depth", "Pending requests", "1", float(metrics.queue_depth)),
        ("aios.inference.active_requests", "Active requests", "1", float(metrics.active_requests)),
        ("aios.inference.error_rate", "Error rate", "1", metrics.error_rate),
        ("aios.gpu.vram_used", "VRAM used", "MB", float(metrics.vram_used_mb)),
        ("aios.gpu.vram_total", "VRAM total", "MB", float(metrics.vram_total_mb)),
        ("aios.gpu.kv_cache_util", "KV cache utilization", "1", metrics.kv_cache_utilization),
        # PSI
        ("aios.psi.memory_some_avg10", "Memory pressure some avg10", "%", pressure.memory_some_avg10),
        ("aios.psi.memory_full_avg10", "Memory pressure full avg10", "%", pressure.memory_full_avg10),
        ("aios.psi.cpu_some_avg10", "CPU pressure some avg10", "%", pressure.cpu_some_avg10),
        ("aios.psi.io_some_avg10", "IO pressure some avg10", "%", pressure.io_some_avg10),
    ]

    for name, desc, unit, value in gauge_metrics:
        points.append({
            "name": name,
            "description": desc,
            "unit": unit,
            "gauge": {
                "dataPoints": [{
                    "asDouble": value,
                    "timeUnixNano": str(now_ns),
                    "attributes": [
                        {"key": "engine", "value": {"stringValue": metrics.engine}},
                        {"key": "model", "value": {"stringValue": metrics.model}},
                    ],
                }]
            },
        })

    return {
        "resourceMetrics": [{
            "resource": resource,
            "scopeMetrics": [{
                "scope": {"name": "aios.metrics", "version": "0.4.0"},
                "metrics": points,
            }],
        }]
    }


def export_metrics(
    metrics: InferenceMetrics,
    pressure: SystemPressure,
    endpoint: str = OTEL_DEFAULT_ENDPOINT,
    node_id: str = "",
    profile: str = "",
    timeout: int = 5,
) -> bool:
    """Export metrics to OTel Collector via OTLP/HTTP."""
    payload = build_metric_payload(metrics, pressure, node_id, profile)
    data = json.dumps(payload).encode()

    try:
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 202)
    except Exception:
        return False
