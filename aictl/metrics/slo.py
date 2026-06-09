"""AI workload metrics schema and collector interface.

Defines the standard metrics for SLO monitoring:
  - TTFT (Time To First Token) — ms
  - ITL  (Inter-Token Latency) — ms
  - TPOT (Time Per Output Token) — ms
  - tokens_per_sec — throughput
  - queue_depth — pending requests
  - vram_used_mb / vram_total_mb
  - kv_cache_utilization — 0.0–1.0
  - error_rate — 0.0–1.0
  - psi_memory — PSI some/full avg10
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class InferenceMetrics:
    timestamp: float = 0.0
    engine: str = ""           # vllm | sglang | ollama | trt-llm
    model: str = ""
    ttft_ms_p50: float = 0.0
    ttft_ms_p95: float = 0.0
    ttft_ms_p99: float = 0.0
    itl_ms_p50: float = 0.0
    itl_ms_p95: float = 0.0
    tpot_ms: float = 0.0
    tokens_per_sec: float = 0.0
    queue_depth: int = 0
    active_requests: int = 0
    vram_used_mb: int = 0
    vram_total_mb: int = 0
    kv_cache_utilization: float = 0.0
    prefix_cache_hit_rate: float = 0.0   # vLLM v1 prefix cache hit ratio (0-1)
    error_rate: float = 0.0
    goodput_ratio: float = 1.0   # fraction of requests meeting latency SLOs (SOLA)


@dataclass
class SystemPressure:
    """Linux PSI (Pressure Stall Information)."""
    memory_some_avg10: float = 0.0
    memory_some_avg60: float = 0.0
    memory_full_avg10: float = 0.0
    memory_full_avg60: float = 0.0
    cpu_some_avg10: float = 0.0
    io_some_avg10: float = 0.0


@dataclass
class SLOTarget:
    """SLO targets for automated governance."""
    ttft_p95_ms: float = 500.0
    itl_p95_ms: float = 50.0
    tokens_per_sec_min: float = 10.0
    error_rate_max: float = 0.05
    queue_depth_max: int = 100
    kv_cache_max: float = 0.9
    goodput_min: float = 0.95           # min fraction of requests meeting latency SLOs
    psi_memory_some_max: float = 25.0   # percentage


@dataclass
class SLOVerdict:
    """SLO check result."""
    compliant: bool = True
    violations: list[str] = field(default_factory=list)
    action: str = "none"  # none | scale_batch | drain | failover


def read_psi() -> SystemPressure:
    """Read Linux PSI from /proc/pressure/."""
    sp = SystemPressure()
    for resource, prefix in [("memory", "memory"), ("cpu", "cpu"), ("io", "io")]:
        path = f"/proc/pressure/{resource}"
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith("some"):
                        parts = line.split()
                        for p in parts:
                            if p.startswith("avg10="):
                                val = float(p.split("=")[1])
                                if resource == "memory":
                                    sp.memory_some_avg10 = val
                                elif resource == "cpu":
                                    sp.cpu_some_avg10 = val
                                elif resource == "io":
                                    sp.io_some_avg10 = val
                            elif p.startswith("avg60="):
                                val = float(p.split("=")[1])
                                if resource == "memory":
                                    sp.memory_some_avg60 = val
                    elif line.startswith("full"):
                        parts = line.split()
                        for p in parts:
                            if p.startswith("avg10="):
                                val = float(p.split("=")[1])
                                if resource == "memory":
                                    sp.memory_full_avg10 = val
                            elif p.startswith("avg60="):
                                val = float(p.split("=")[1])
                                if resource == "memory":
                                    sp.memory_full_avg60 = val
        except (OSError, ValueError):
            pass  # best-effort; failure is non-critical
    return sp


def check_slo(metrics: InferenceMetrics, pressure: SystemPressure,
              target: SLOTarget) -> SLOVerdict:
    """Check if current metrics meet SLO targets."""
    v = SLOVerdict()

    if metrics.ttft_ms_p95 > target.ttft_p95_ms:
        v.violations.append(f"TTFT p95 {metrics.ttft_ms_p95:.0f}ms > {target.ttft_p95_ms:.0f}ms")
    if metrics.itl_ms_p95 > target.itl_p95_ms:
        v.violations.append(f"ITL p95 {metrics.itl_ms_p95:.0f}ms > {target.itl_p95_ms:.0f}ms")
    if metrics.tokens_per_sec < target.tokens_per_sec_min and metrics.active_requests > 0:
        v.violations.append(f"Throughput {metrics.tokens_per_sec:.1f} t/s < {target.tokens_per_sec_min:.1f} t/s")
    if metrics.error_rate > target.error_rate_max:
        v.violations.append(f"Error rate {metrics.error_rate:.1%} > {target.error_rate_max:.1%}")
    if metrics.queue_depth > target.queue_depth_max:
        v.violations.append(f"Queue depth {metrics.queue_depth} > {target.queue_depth_max}")
    if metrics.kv_cache_utilization > target.kv_cache_max:
        v.violations.append(f"KV cache {metrics.kv_cache_utilization:.1%} > {target.kv_cache_max:.1%}")
    if metrics.goodput_ratio < target.goodput_min and metrics.active_requests > 0:
        v.violations.append(f"Goodput {metrics.goodput_ratio:.1%} < {target.goodput_min:.1%}")
    if pressure.memory_some_avg10 > target.psi_memory_some_max:
        v.violations.append(f"PSI memory {pressure.memory_some_avg10:.1f}% > {target.psi_memory_some_max:.1f}%")

    v.compliant = len(v.violations) == 0

    # Determine action
    if not v.compliant:
        critical = any("Error rate" in vi or "PSI" in vi for vi in v.violations)
        if critical:
            v.action = "failover"
        elif len(v.violations) >= 3:
            v.action = "drain"
        else:
            v.action = "scale_batch"

    return v


@dataclass
class GoodputResult:
    """Goodput = throughput that meets latency SLOs (SOLA, arXiv).

    Unlike raw throughput (tokens/sec regardless of latency), goodput counts
    only requests that satisfied *all* latency constraints (TTFT and TPOT).
    It is the system-level metric operators actually care about: serving fast
    is worthless if responses violate the SLO the user was promised.
    """
    total_requests: int = 0
    slo_met_requests: int = 0
    goodput_ratio: float = 0.0       # slo_met / total, in [0, 1]
    goodput_rps: float = 0.0          # SLO-satisfied requests per second
    ttft_violations: int = 0
    tpot_violations: int = 0


def compute_goodput(
    samples: list[tuple[float, float]],
    target: SLOTarget,
    window_seconds: float = 0.0,
) -> GoodputResult:
    """Compute goodput from per-request (ttft_ms, tpot_ms) samples.

    A request counts toward goodput only if BOTH its TTFT and TPOT met the
    SLO target (ttft_p95_ms and itl_p95_ms respectively — itl is the
    per-output-token target, equivalent to TPOT). Reference: SOLA / SCORPIO
    define goodput as latency-constrained throughput.

    Args:
        samples: list of (ttft_ms, tpot_ms) per completed request.
        target: SLO thresholds to check against.
        window_seconds: wall-clock window the samples cover; if > 0, also
            computes goodput in SLO-satisfied requests per second.

    Returns:
        GoodputResult with ratio, per-second rate, and violation breakdown.
    """
    result = GoodputResult(total_requests=len(samples))
    if not samples:
        return result

    for ttft_ms, tpot_ms in samples:
        ttft_ok = ttft_ms <= target.ttft_p95_ms
        tpot_ok = tpot_ms <= target.itl_p95_ms
        if not ttft_ok:
            result.ttft_violations += 1
        if not tpot_ok:
            result.tpot_violations += 1
        if ttft_ok and tpot_ok:
            result.slo_met_requests += 1

    result.goodput_ratio = result.slo_met_requests / result.total_requests
    if window_seconds > 0:
        result.goodput_rps = result.slo_met_requests / window_seconds
    return result


def goodput_from_spans(
    spans_path: str,
    target: SLOTarget,
    max_spans: int = 1000,
) -> GoodputResult:
    """Compute live goodput from recorded GenAI spans (genai_spans.jsonl).

    Reads the most recent spans the proxy wrote per request, derives TPOT from
    decode time / output tokens, and feeds (ttft_ms, tpot_ms) pairs to
    compute_goodput. This turns goodput from an offline-only metric into one
    that reflects real served traffic.

    TPOT derivation: (duration_ms - ttft_ms) / max(output_tokens - 1, 1).
    The first token's latency is TTFT; the remaining tokens' average is TPOT.

    Args:
        spans_path: path to the JSONL span log written by the proxy.
        target: SLO thresholds (ttft_p95_ms, itl_p95_ms).
        max_spans: cap on how many recent spans to consider.

    Returns:
        GoodputResult over the recent window; empty result if no spans.
    """
    import json

    if not os.path.exists(spans_path):
        return GoodputResult()

    samples: list[tuple[float, float]] = []
    earliest_ns = 0
    latest_ns = 0
    try:
        with open(spans_path) as f:
            lines = f.readlines()
    except OSError:
        return GoodputResult()

    for line in lines[-max_spans:]:
        line = line.strip()
        if not line:
            continue
        try:
            span = json.loads(line)
        except json.JSONDecodeError:
            continue
        ttft_ms = float(span.get("ttft_ms", 0.0))
        out_tokens = int(span.get("output_tokens", 0))
        start_ns = int(span.get("start_time_ns", 0))
        end_ns = int(span.get("end_time_ns", 0))
        if ttft_ms <= 0 or out_tokens <= 0 or end_ns <= start_ns:
            continue  # incomplete span; skip
        duration_ms = (end_ns - start_ns) / 1_000_000
        decode_ms = max(duration_ms - ttft_ms, 0.0)
        tpot_ms = decode_ms / max(out_tokens - 1, 1)
        samples.append((ttft_ms, tpot_ms))
        if earliest_ns == 0 or start_ns < earliest_ns:
            earliest_ns = start_ns
        if end_ns > latest_ns:
            latest_ns = end_ns

    window_s = (latest_ns - earliest_ns) / 1_000_000_000 if latest_ns > earliest_ns else 0.0
    return compute_goodput(samples, target, window_seconds=window_s)
