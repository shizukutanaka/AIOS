"""OpenTelemetry GenAI Semantic Conventions for LLM inference spans.

Implements the OTel GenAI SemConv (experimental, March 2026) for
standardized LLM observability. These attributes work with:
  - Datadog (native support since OTel v1.37)
  - Grafana/Loki
  - Jaeger
  - Arize Phoenix
  - Any OTel-compatible backend

Key attributes (gen_ai.* namespace):
  gen_ai.system           — inference engine (vllm, sglang, ollama)
  gen_ai.request.model    — model name requested
  gen_ai.response.model   — model name in response
  gen_ai.operation.name   — operation (chat, completion, embedding)
  gen_ai.usage.input_tokens  — prompt token count
  gen_ai.usage.output_tokens — completion token count
  gen_ai.response.finish_reasons — ["stop", "length", "tool_calls"]
  gen_ai.request.max_tokens    — max_tokens parameter
  gen_ai.request.temperature   — temperature parameter

Spec: https://opentelemetry.io/docs/specs/semconv/gen-ai/
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenAISpan:
    """A single GenAI inference span following OTel SemConv."""
    # Required
    operation: str = "chat"           # chat | completion | embedding
    system: str = "vllm"             # vllm | sglang | ollama | cloud
    request_model: str = ""

    # Response
    response_model: str = ""
    finish_reasons: list[str] = field(default_factory=lambda: ["stop"])

    # Usage
    input_tokens: int = 0
    output_tokens: int = 0

    # Request params
    max_tokens: int = 0
    temperature: float = 0.0
    top_p: float = 0.0

    # Timing (nanoseconds)
    start_time_ns: int = 0
    end_time_ns: int = 0
    ttft_ms: float = 0.0             # Time to first token

    # aictl-specific
    engine_endpoint: str = ""
    router_score: float = 0.0
    fallback_provider: str = ""       # Non-empty if cloud fallback used
    api_key_id: str = ""              # For per-key attribution

    def duration_ms(self) -> float:
        """Execute duration ms."""
        if self.start_time_ns and self.end_time_ns:
            return (self.end_time_ns - self.start_time_ns) / 1_000_000
        return 0.0

    def to_otel_attributes(self) -> dict[str, Any]:
        """Convert to OTel GenAI semantic convention attributes."""
        attrs: dict[str, Any] = {
            "gen_ai.system": self.system,
            "gen_ai.operation.name": self.operation,
            "gen_ai.request.model": self.request_model,
        }

        if self.response_model:
            attrs["gen_ai.response.model"] = self.response_model
        if self.finish_reasons:
            attrs["gen_ai.response.finish_reasons"] = self.finish_reasons
        if self.input_tokens:
            attrs["gen_ai.usage.input_tokens"] = self.input_tokens
        if self.output_tokens:
            attrs["gen_ai.usage.output_tokens"] = self.output_tokens
        if self.max_tokens:
            attrs["gen_ai.request.max_tokens"] = self.max_tokens
        if self.temperature:
            attrs["gen_ai.request.temperature"] = self.temperature
        if self.top_p:
            attrs["gen_ai.request.top_p"] = self.top_p

        # aictl extensions (vendor-specific, using aios. prefix)
        if self.engine_endpoint:
            attrs["aios.engine.endpoint"] = self.engine_endpoint
        if self.router_score:
            attrs["aios.router.score"] = self.router_score
        if self.fallback_provider:
            attrs["aios.fallback.provider"] = self.fallback_provider
        if self.api_key_id:
            attrs["aios.apikey.id"] = self.api_key_id
        if self.ttft_ms:
            attrs["aios.ttft_ms"] = self.ttft_ms

        return attrs

    def to_otlp_span(self, service_name: str = "aictl") -> dict[str, Any]:
        """Convert to OTLP JSON span format for export."""
        import hashlib
        span_id = hashlib.sha256(
            f"{self.start_time_ns}{self.request_model}".encode()
        ).hexdigest()[:16]
        trace_id = hashlib.sha256(
            f"{self.start_time_ns}{service_name}".encode()
        ).hexdigest()[:32]

        return {
            "traceId": trace_id,
            "spanId": span_id,
            "name": f"{self.operation} {self.request_model}",
            "kind": 3,  # SPAN_KIND_CLIENT
            "startTimeUnixNano": str(self.start_time_ns),
            "endTimeUnixNano": str(self.end_time_ns),
            "attributes": [
                {"key": k, "value": _otel_value(v)}
                for k, v in self.to_otel_attributes().items()
            ],
            "status": {"code": 1},  # STATUS_CODE_OK
        }


def export_spans(spans: list[GenAISpan],
                 endpoint: str = "http://localhost:4318/v1/traces",
                 service_name: str = "aictl") -> bool:
    """Export GenAI spans to an OTel collector via OTLP/HTTP JSON."""
    if not spans:
        return True

    payload = {
        "resourceSpans": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": service_name}},
                    {"key": "service.version", "value": {"stringValue": "1.5.0"}},
                ],
            },
            "scopeSpans": [{
                "scope": {"name": "aictl.genai", "version": "1.5.0"},
                "spans": [s.to_otlp_span(service_name) for s in spans],
            }],
        }],
    }

    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            endpoint, data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def span_from_proxy_request(
    request_body: dict[str, Any],
    response_body: dict[str, Any],
    engine: str = "vllm",
    endpoint: str = "",
    start_ns: int = 0,
    end_ns: int = 0,
) -> GenAISpan:
    """Create a GenAI span from proxy request/response data."""
    usage = response_body.get("usage", {})
    model = response_body.get("model", request_body.get("model", ""))
    choices = response_body.get("choices", [])
    finish = [c.get("finish_reason", "stop") for c in choices if c.get("finish_reason")]

    return GenAISpan(
        operation="chat" if "messages" in request_body else "completion",
        system=engine,
        request_model=request_body.get("model", ""),
        response_model=model,
        finish_reasons=finish or ["stop"],
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        max_tokens=request_body.get("max_tokens", 0),
        temperature=request_body.get("temperature", 0.0),
        top_p=request_body.get("top_p", 0.0),
        engine_endpoint=endpoint,
        start_time_ns=start_ns,
        end_time_ns=end_ns,
    )


def _otel_value(v: Any) -> dict[str, Any]:
    """Convert Python value to OTLP attribute value."""
    if isinstance(v, str):
        return {"stringValue": v}
    elif isinstance(v, bool):
        return {"boolValue": v}
    elif isinstance(v, int):
        return {"intValue": str(v)}
    elif isinstance(v, float):
        return {"doubleValue": v}
    elif isinstance(v, list):
        return {"arrayValue": {"values": [_otel_value(i) for i in v]}}
    return {"stringValue": str(v)}
