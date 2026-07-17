"""Prometheus alerting-rules generation from SLO targets.

Emits a Prometheus rule-group YAML (stdlib string rendering, no PyYAML)
so operators get alerting out of the box that matches the SLO governor's
own thresholds.
"""

from __future__ import annotations

from typing import Any


def _rule(name: str, expr: str, duration: str, severity: str, summary: str) -> str:
    """Render a single Prometheus alerting rule block."""
    return (
        f"    - alert: {name}\n"
        f"      expr: {expr}\n"
        f"      for: {duration}\n"
        f"      labels:\n"
        f"        severity: {severity}\n"
        f"      annotations:\n"
        f"        summary: \"{summary}\"\n"
    )


def generate_alert_rules(target: Any = None) -> str:
    """Generate a Prometheus alerting rules YAML from an SLOTarget.

    If target is None, uses the default SLOTarget thresholds.
    """
    if target is None:
        from aictl.metrics.slo import SLOTarget
        target = SLOTarget()

    rules: list[str] = []

    rules.append(_rule(
        "AIOSEngineDown",
        "aios_engine_reachable == 0",
        "1m", "critical",
        "Inference engine {{ $labels.engine }} is unreachable",
    ))
    rules.append(_rule(
        "AIOSHighErrorRate",
        f"aios_inference_error_rate > {target.error_rate_max}",
        "5m", "warning",
        f"Inference error rate above {target.error_rate_max:.0%} SLO",
    ))
    rules.append(_rule(
        "AIOSQueueDepthHigh",
        f"aios_inference_queue_depth > {target.queue_depth_max}",
        "5m", "warning",
        f"Inference queue depth above {target.queue_depth_max} (backpressure)",
    ))
    rules.append(_rule(
        "AIOSKVCacheSaturated",
        f"aios_inference_kv_cache_utilization > {target.kv_cache_max}",
        "5m", "warning",
        f"KV cache utilization above {target.kv_cache_max:.0%} (risk of preemption)",
    ))
    rules.append(_rule(
        "AIOSThroughputLow",
        f"aios_inference_throughput_tokens_per_sec < {target.tokens_per_sec_min}",
        "10m", "warning",
        f"Throughput below {target.tokens_per_sec_min} tokens/sec SLO floor",
    ))
    rules.append(_rule(
        "AIOSMemoryPressureHigh",
        f"aios_psi_memory_some_avg10 > {target.psi_memory_some_max}",
        "5m", "warning",
        f"Memory pressure (PSI some avg10) above {target.psi_memory_some_max:.0f}%",
    ))

    body = "".join(rules)
    return (
        "groups:\n"
        "  - name: aios_slo_alerts\n"
        "    rules:\n"
        f"{body}"
    )
