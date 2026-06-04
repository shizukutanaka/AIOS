"""Generate OTel Collector configuration for AI workload monitoring.

Based on research: OTel Collector v0.149.0, GenAI SemConv v1.40.0
Scrapes vLLM/SGLang Prometheus endpoints and exports to Grafana/Mimir.
"""

from __future__ import annotations

from aictl.core.config import Config


def generate_otel_config(config: Config, output_format: str = "yaml") -> str:
    """Generate an OTel Collector config that scrapes inference engines."""
    endpoints = config.engines
    targets = []
    if endpoints.vllm:
        host = endpoints.vllm.replace("http://", "").replace("https://", "")
        targets.append(f"'{host}'")
    if endpoints.sglang:
        host = endpoints.sglang.replace("http://", "").replace("https://", "")
        targets.append(f"'{host}'")

    return f"""# OTel Collector config for AI OS (auto-generated)
# Scrapes vLLM/SGLang Prometheus metrics and exports via OTLP

receivers:
  prometheus:
    config:
      scrape_configs:
        - job_name: 'aios-inference'
          scrape_interval: 15s
          static_configs:
            - targets: [{', '.join(targets)}]
          metric_relabel_configs:
            - source_labels: [__name__]
              regex: '(vllm|sglang)_.*'
              action: keep

  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 10s
    send_batch_size: 1024
  memory_limiter:
    check_interval: 5s
    limit_mib: 256

exporters:
  prometheusremotewrite:
    endpoint: http://localhost:9009/api/v1/push
  debug:
    verbosity: basic

service:
  pipelines:
    metrics:
      receivers: [prometheus, otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheusremotewrite, debug]
"""
