"""Tests for OTel GenAI Semantic Conventions."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.metrics.genai_spans import (
    GenAISpan, span_from_proxy_request, _otel_value,
)


class TestGenAISpan(unittest.TestCase):
    def test_basic_attributes(self):
        span = GenAISpan(
            system="vllm", operation="chat",
            request_model="llama3-8b",
            input_tokens=100, output_tokens=50,
        )
        attrs = span.to_otel_attributes()
        self.assertEqual(attrs["gen_ai.system"], "vllm")
        self.assertEqual(attrs["gen_ai.operation.name"], "chat")
        self.assertEqual(attrs["gen_ai.request.model"], "llama3-8b")
        self.assertEqual(attrs["gen_ai.usage.input_tokens"], 100)
        self.assertEqual(attrs["gen_ai.usage.output_tokens"], 50)

    def test_optional_attributes(self):
        span = GenAISpan(
            system="sglang", request_model="qwen3",
            temperature=0.7, top_p=0.9, max_tokens=1000,
        )
        attrs = span.to_otel_attributes()
        self.assertAlmostEqual(attrs["gen_ai.request.temperature"], 0.7)
        self.assertAlmostEqual(attrs["gen_ai.request.top_p"], 0.9)
        self.assertEqual(attrs["gen_ai.request.max_tokens"], 1000)

    def test_aios_extensions(self):
        span = GenAISpan(
            system="vllm", request_model="llama3",
            engine_endpoint="http://gpu1:8000",
            router_score=0.95,
            fallback_provider="openrouter",
        )
        attrs = span.to_otel_attributes()
        self.assertEqual(attrs["aios.engine.endpoint"], "http://gpu1:8000")
        self.assertAlmostEqual(attrs["aios.router.score"], 0.95)
        self.assertEqual(attrs["aios.fallback.provider"], "openrouter")

    def test_duration(self):
        span = GenAISpan(
            start_time_ns=1_000_000_000, end_time_ns=1_050_000_000,
        )
        self.assertAlmostEqual(span.duration_ms(), 50.0)

    def test_to_otlp_span(self):
        span = GenAISpan(
            system="vllm", operation="chat", request_model="llama3",
            start_time_ns=1000, end_time_ns=2000,
        )
        otlp = span.to_otlp_span()
        self.assertEqual(otlp["name"], "chat llama3")
        self.assertEqual(otlp["kind"], 3)  # CLIENT
        self.assertIn("traceId", otlp)
        self.assertIn("spanId", otlp)

    def test_finish_reasons(self):
        span = GenAISpan(finish_reasons=["stop", "length"])
        attrs = span.to_otel_attributes()
        self.assertEqual(attrs["gen_ai.response.finish_reasons"], ["stop", "length"])


class TestSpanFromProxy(unittest.TestCase):
    def test_from_completion(self):
        req = {"model": "llama3", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 100}
        resp = {
            "model": "llama3",
            "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10},
        }
        span = span_from_proxy_request(req, resp, engine="vllm", start_ns=1000, end_ns=2000)
        self.assertEqual(span.operation, "chat")
        self.assertEqual(span.system, "vllm")
        self.assertEqual(span.input_tokens, 5)
        self.assertEqual(span.output_tokens, 10)
        self.assertEqual(span.finish_reasons, ["stop"])

    def test_completion_without_messages(self):
        req = {"model": "llama3", "prompt": "hello"}
        resp = {"model": "llama3", "choices": [], "usage": {}}
        span = span_from_proxy_request(req, resp)
        self.assertEqual(span.operation, "completion")


class TestOTelValue(unittest.TestCase):
    def test_string(self):
        self.assertEqual(_otel_value("hello"), {"stringValue": "hello"})

    def test_int(self):
        self.assertEqual(_otel_value(42), {"intValue": "42"})

    def test_float(self):
        self.assertEqual(_otel_value(3.14), {"doubleValue": 3.14})

    def test_list(self):
        result = _otel_value(["a", "b"])
        self.assertIn("arrayValue", result)


if __name__ == "__main__":
    unittest.main()
