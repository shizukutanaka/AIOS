"""Pass 13 regression tests for correctness bugs identified by deep audit."""

import unittest
from unittest import mock


class TestGenaiSpansAccepts202(unittest.TestCase):
    """genai_spans.py: OTLP exporters must accept HTTP 202 Accepted, not only 200.

    The OTLP/HTTP spec allows collectors to respond with 202 Accepted. The
    sibling otel.py exporter already accepts (200, 202); genai_spans.py was
    rejecting valid 202 responses as failures.
    """

    def test_source_accepts_202(self):
        import pathlib, re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "metrics" / "genai_spans.py").read_text()
        # There must be no `resp.status == 200` exact-match check left (it rejects 202)
        bad = re.findall(r"resp\.status\s*==\s*200", src)
        self.assertEqual(
            bad, [],
            "genai_spans.py must accept 202 (OTLP Accepted); use `resp.status in (200, 202)`",
        )
        # And the tolerant form must be present
        self.assertIn("resp.status in (200, 202)", src)

    def _make_resp(self, status):
        m = mock.MagicMock()
        m.status = status
        m.__enter__ = lambda s: m
        m.__exit__ = lambda s, *a: False
        return m

    def test_export_tool_spans_returns_true_on_202(self):
        from aictl.metrics.genai_spans import export_tool_spans, ToolSpan
        span = ToolSpan(
            tool_name="search",
            start_time_ns=1_000_000,
            end_time_ns=2_000_000,
        )
        with mock.patch("urllib.request.urlopen", return_value=self._make_resp(202)):
            result = export_tool_spans([span])
        self.assertTrue(result, "202 Accepted from OTLP collector must count as success")

    def test_export_spans_returns_true_on_202(self):
        from aictl.metrics.genai_spans import export_spans, GenAISpan
        span = GenAISpan(
            request_model="mock",
            start_time_ns=1_000_000,
            end_time_ns=2_000_000,
        )
        with mock.patch("urllib.request.urlopen", return_value=self._make_resp(202)):
            result = export_spans([span])
        self.assertTrue(result, "202 Accepted from OTLP collector must count as success")

    def test_export_spans_returns_true_on_200(self):
        from aictl.metrics.genai_spans import export_spans, GenAISpan
        span = GenAISpan(request_model="mock", start_time_ns=1_000_000, end_time_ns=2_000_000)
        with mock.patch("urllib.request.urlopen", return_value=self._make_resp(200)):
            result = export_spans([span])
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
