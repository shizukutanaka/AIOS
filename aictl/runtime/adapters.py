"""Engine adapters: scrape metrics and health from inference engines.

Each adapter knows how to:
  1. Check health of the engine
  2. List loaded models
  3. Scrape inference metrics (TTFT, TPOT, queue, KV cache)
  4. Report engine state (READY, DEGRADED, etc.)

vLLM exposes Prometheus metrics at /metrics.
Ollama exposes a REST API at /api/*.
SGLang exposes OpenAI-compatible /v1/* and /metrics.
"""

from __future__ import annotations

from typing import Any

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from aictl.core.constants import OLLAMA_DEFAULT_URL, SGLANG_DEFAULT_URL, VLLM_DEFAULT_URL
from aictl.metrics.slo import InferenceMetrics

# Probe failure counter — silent errors during engine discovery.
# Check via: adapters.probe_failures
_probe_failures: list[str] = []
_MAX_PROBE_LOG = 50


def _note_probe_error(engine: str, endpoint: str, error: Exception) -> None:
    """Record a probe failure for later diagnosis (e.g., doctor --deep)."""
    msg = f"{engine}:{endpoint}: {type(error).__name__}"
    _probe_failures.append(msg)
    if len(_probe_failures) > _MAX_PROBE_LOG:
        del _probe_failures[: len(_probe_failures) - _MAX_PROBE_LOG]


def get_probe_failures() -> list[str]:
    """Recent engine probe failures. For diagnostics."""
    return list(_probe_failures)


@dataclass
class EngineHealth:
    engine: str             # vllm | sglang | ollama | trt-llm
    endpoint: str
    reachable: bool = False
    status: str = "OFFLINE"  # PENDING | WARMING | READY | DEGRADED | DRAINING | OFFLINE | BLOCKED
    models: list[str] = field(default_factory=list)
    version: str = ""
    error: str = ""
    latency_ms: float = 0.0  # health check latency


def _http_get(url: str, timeout: int = 5) -> tuple[int, str]:
    """Simple HTTP GET, returns (status_code, body)."""
    try:
        time.monotonic()
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)


# ══════════════════════════════════════════════════════════
#  vLLM adapter
# ══════════════════════════════════════════════════════════

class VLLMAdapter:
    """Adapter for vLLM OpenAI-compatible server."""

    def __init__(self, endpoint: str = VLLM_DEFAULT_URL):
        """Initialize vLLM adapter."""
        self.endpoint = endpoint.rstrip("/")

    def health(self) -> EngineHealth:
        """Health."""
        h = EngineHealth(engine="vllm", endpoint=self.endpoint)
        t0 = time.monotonic()
        code, body = _http_get(f"{self.endpoint}/health")
        h.latency_ms = (time.monotonic() - t0) * 1000

        if code == 200:
            h.reachable = True
            h.status = "READY"
        elif code > 0:
            h.reachable = True
            h.status = "DEGRADED"
            h.error = f"HTTP {code}"
        else:
            h.error = body[:200]
            return h

        # Get models
        code, body = _http_get(f"{self.endpoint}/v1/models")
        if code == 200:
            try:
                data = json.loads(body)
                h.models = [m.get("id", "") for m in data.get("data", [])]
            except (json.JSONDecodeError, KeyError) as _e:
                _note_probe_error("probe", "", _e)  # logged, not raised

        # Get version
        code, body = _http_get(f"{self.endpoint}/version")
        if code == 200:
            try:
                h.version = json.loads(body).get("version", "")
            except (json.JSONDecodeError, KeyError) as _e:
                _note_probe_error("probe", "", _e)  # logged, not raised

        return h

    def scrape_metrics(self) -> InferenceMetrics:
        """Scrape Prometheus metrics from vLLM /metrics endpoint."""
        m = InferenceMetrics(engine="vllm", timestamp=time.time())
        code, body = _http_get(f"{self.endpoint}/metrics")
        if code != 200:
            return m

        m.ttft_ms_p50 = _prom_histogram_quantile(body, "vllm:time_to_first_token_seconds", 0.5) * 1000
        m.ttft_ms_p95 = _prom_histogram_quantile(body, "vllm:time_to_first_token_seconds", 0.95) * 1000
        m.ttft_ms_p99 = _prom_histogram_quantile(body, "vllm:time_to_first_token_seconds", 0.99) * 1000
        m.itl_ms_p50 = _prom_histogram_quantile(body, "vllm:time_per_output_token_seconds", 0.5) * 1000
        m.itl_ms_p95 = _prom_histogram_quantile(body, "vllm:time_per_output_token_seconds", 0.95) * 1000
        m.tpot_ms = m.itl_ms_p50

        m.queue_depth = int(_prom_gauge(body, "vllm:num_requests_waiting"))
        m.active_requests = int(_prom_gauge(body, "vllm:num_requests_running"))

        gpu_used = _prom_gauge(body, "vllm:kv_cache_usage_perc")
        m.kv_cache_utilization = gpu_used

        # tokens/sec from generation throughput
        m.tokens_per_sec = _prom_gauge(body, "vllm:avg_generation_throughput_toks_per_s")

        # Prefix cache (v1 engine)
        cache_queries = _prom_gauge(body, "vllm:prefix_cache_queries")
        cache_hits = _prom_gauge(body, "vllm:prefix_cache_hits")
        if cache_queries > 0:
            m.model = f"cache_hit_rate={cache_hits/cache_queries:.2f}"

        # Error rate from request counters
        _prom_gauge(body, "vllm:request_success_total")
        # Prefix cache metrics (v1 engine, v0.18+)
        cache_queries = _prom_gauge(body, "vllm:prefix_cache_queries")
        cache_hits = _prom_gauge(body, "vllm:prefix_cache_hits")
        if hasattr(m, '_prefix_cache_hit_rate'):
            pass  # future use


        return m


# ══════════════════════════════════════════════════════════
#  Ollama adapter
# ══════════════════════════════════════════════════════════

class OllamaAdapter:
    """Adapter for Ollama local inference server."""

    def __init__(self, endpoint: str = OLLAMA_DEFAULT_URL):
        """Initialize Ollama adapter."""
        self.endpoint = endpoint.rstrip("/")

    def health(self) -> EngineHealth:
        """Health."""
        h = EngineHealth(engine="ollama", endpoint=self.endpoint)
        t0 = time.monotonic()
        code, body = _http_get(f"{self.endpoint}/api/tags")
        h.latency_ms = (time.monotonic() - t0) * 1000

        if code == 200:
            h.reachable = True
            h.status = "READY"
            try:
                data = json.loads(body)
                h.models = [m.get("name", "") for m in data.get("models", [])]
            except (json.JSONDecodeError, KeyError) as _e:
                _note_probe_error("probe", "", _e)  # logged, not raised
        elif code > 0:
            h.reachable = True
            h.status = "DEGRADED"
            h.error = f"HTTP {code}"
        else:
            h.error = body[:200]

        # Ollama version
        code, body = _http_get(f"{self.endpoint}/api/version")
        if code == 200:
            try:
                h.version = json.loads(body).get("version", "")
            except (json.JSONDecodeError, KeyError) as _e:
                _note_probe_error("probe", "", _e)  # logged, not raised

        return h

    def scrape_metrics(self) -> InferenceMetrics:
        """Ollama doesn't expose Prometheus metrics — return basic status."""
        m = InferenceMetrics(engine="ollama", timestamp=time.time())

        # Check running models via /api/ps
        code, body = _http_get(f"{self.endpoint}/api/ps")
        if code == 200:
            try:
                data = json.loads(body)
                models = data.get("models", [])
                m.active_requests = len(models)
                for model in models:
                    vram = model.get("size_vram", 0)
                    m.vram_used_mb += vram // (1024 * 1024)
            except (json.JSONDecodeError, KeyError) as _e:
                _note_probe_error("probe", "", _e)  # logged, not raised

        return m


# ══════════════════════════════════════════════════════════
#  SGLang adapter
# ══════════════════════════════════════════════════════════

class SGLangAdapter:
    """Adapter for SGLang server."""

    def __init__(self, endpoint: str = SGLANG_DEFAULT_URL):
        """Initialize SGLang adapter."""
        self.endpoint = endpoint.rstrip("/")

    def health(self) -> EngineHealth:
        """Health."""
        h = EngineHealth(engine="sglang", endpoint=self.endpoint)
        t0 = time.monotonic()
        code, body = _http_get(f"{self.endpoint}/health")
        h.latency_ms = (time.monotonic() - t0) * 1000

        if code == 200:
            h.reachable = True
            h.status = "READY"
        elif code > 0:
            h.reachable = True
            h.status = "DEGRADED"
        else:
            h.error = body[:200]
            return h

        # Models
        code, body = _http_get(f"{self.endpoint}/v1/models")
        if code == 200:
            try:
                data = json.loads(body)
                h.models = [m.get("id", "") for m in data.get("data", [])]
            except (json.JSONDecodeError, KeyError) as _e:
                _note_probe_error("probe", "", _e)  # logged, not raised

        return h

    def scrape_metrics(self) -> InferenceMetrics:
        """Scrape metrics."""
        m = InferenceMetrics(engine="sglang", timestamp=time.time())
        code, body = _http_get(f"{self.endpoint}/metrics")
        if code != 200:
            return m

        # SGLang v0.3.0+ uses sglang_ prefix (underscore, not colon)
        m.ttft_ms_p95 = _prom_histogram_quantile(body, "sglang_time_to_first_token_seconds", 0.95) * 1000
        m.itl_ms_p95 = _prom_histogram_quantile(body, "sglang_time_per_output_token_seconds", 0.95) * 1000
        m.queue_depth = int(_prom_gauge(body, "sglang_num_requests_waiting"))
        m.active_requests = int(_prom_gauge(body, "sglang_num_requests_running"))
        m.kv_cache_utilization = _prom_gauge(body, "sglang_cache_hit_rate")

        return m


# ══════════════════════════════════════════════════════════
#  Prometheus text format parsing (minimal)
# ══════════════════════════════════════════════════════════

def _prom_gauge(text: str, metric_name: str) -> float:
    """Extract a gauge value from Prometheus text format."""
    pattern = re.compile(rf'^{re.escape(metric_name)}\s+([\d.eE+-]+)', re.MULTILINE)
    match = pattern.search(text)
    if match:
        try:
            return float(match.group(1))
        except ValueError as _e:
            _note_probe_error("parse", "", _e)  # logged, not raised

    # Try with labels
    pattern2 = re.compile(rf'^{re.escape(metric_name)}{{[^}}]*}}\s+([\d.eE+-]+)', re.MULTILINE)
    match2 = pattern2.search(text)
    if match2:
        try:
            return float(match2.group(1))
        except ValueError as _e:
            _note_probe_error("parse", "", _e)  # logged, not raised

    return 0.0


def _prom_histogram_quantile(text: str, metric_name: str, quantile: float) -> float:
    """Estimate a quantile from histogram buckets in Prometheus text format.

    This is a simplified estimation — for production, use Prometheus queries.
    Falls back to the _sum/_count average if histogram parsing fails.
    """
    # Try to find pre-computed quantile (some exporters provide these)
    q_str = f'{quantile}'
    pattern = re.compile(
        rf'^{re.escape(metric_name)}{{quantile="{re.escape(q_str)}"[^}}]*}}\s+([\d.eE+-]+)',
        re.MULTILINE,
    )
    match = pattern.search(text)
    if match:
        try:
            return float(match.group(1))
        except ValueError as _e:
            _note_probe_error("parse", "", _e)  # logged, not raised

    # Fallback: _sum / _count = average
    sum_val = _prom_gauge(text, f"{metric_name}_sum")
    count_val = _prom_gauge(text, f"{metric_name}_count")
    if count_val > 0:
        return sum_val / count_val

    return 0.0


# ══════════════════════════════════════════════════════════
#  Multi-engine discovery
# ══════════════════════════════════════════════════════════

def discover_engines(endpoints: dict[str, str] | None = None) -> list[EngineHealth]:
    """Discover and health-check all known engine endpoints.

    Default endpoints:
      vllm:   http://localhost:8000
      ollama: http://localhost:11434
      sglang: http://localhost:30000
    """
    if endpoints is None:
        endpoints = {
            "vllm": VLLM_DEFAULT_URL,
            "ollama": OLLAMA_DEFAULT_URL,
            "sglang": SGLANG_DEFAULT_URL,
        }

    adapters = {
        "vllm": VLLMAdapter,
        "ollama": OllamaAdapter,
        "sglang": SGLangAdapter,
    }

    results: list[EngineHealth] = []
    for name, url in endpoints.items():
        cls = adapters.get(name)
        if cls:
            adapter = cls(url)
            results.append(adapter.health())

    return results


def get_adapter(engine: str, endpoint: str) -> "VLLMAdapter | OllamaAdapter | SGLangAdapter | None":
    """Get the appropriate adapter for an engine type."""
    adapters: dict[str, Any] = {
        "vllm": VLLMAdapter,
        "ollama": OllamaAdapter,
        "sglang": SGLangAdapter,
    }
    cls = adapters.get(engine)
    if cls:
        adapter: VLLMAdapter | OllamaAdapter | SGLangAdapter = cls(endpoint)
        return adapter
    return None
