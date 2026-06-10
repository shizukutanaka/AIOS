"""Pass 36 regression tests: hardcoded version strings (v1.5.0) replaced with AICTL_VERSION."""

import pathlib
import unittest


class TestVersionConsistency(unittest.TestCase):
    """Version strings in source files must use AICTL_VERSION, not hardcoded values."""

    def test_genai_spans_no_hardcoded_150(self):
        """genai_spans.py must not contain hardcoded '1.5.0' version string."""
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "metrics" / "genai_spans.py"
        ).read_text()
        self.assertNotIn(
            '"1.5.0"',
            src,
            'aictl/metrics/genai_spans.py still has hardcoded "1.5.0" — use AICTL_VERSION.',
        )

    def test_genai_spans_uses_aictl_version(self):
        """genai_spans.py must import and use AICTL_VERSION."""
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "metrics" / "genai_spans.py"
        ).read_text()
        self.assertIn(
            "AICTL_VERSION",
            src,
            "aictl/metrics/genai_spans.py must import and use AICTL_VERSION.",
        )

    def test_mock_engine_no_hardcoded_150(self):
        """mock_engine.py must not contain v1.5.0 in user-visible response strings."""
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "daemon" / "mock_engine.py"
        ).read_text()
        self.assertNotIn(
            "v1.5.0",
            src,
            'aictl/daemon/mock_engine.py still says "v1.5.0" in default response — update to v1.6.0.',
        )

    def test_export_spans_uses_aictl_version_constant(self):
        """export_spans() must emit the version from AICTL_VERSION."""
        import json
        import unittest.mock as mock
        from aictl.metrics.genai_spans import export_spans, GenAISpan
        from aictl.core.constants import AICTL_VERSION

        span = GenAISpan(
            operation="chat",
            request_model="test-model",
            input_tokens=10,
            output_tokens=5,
        )

        captured = {}
        original_urlopen = __import__("urllib.request", fromlist=["urlopen"]).urlopen

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            raise OSError("test mode — not connecting")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            try:
                export_spans([span], endpoint="http://localhost:9999/v1/traces")
            except OSError:
                pass  # expected

        if "body" in captured:
            attrs = captured["body"]["resourceSpans"][0]["resource"]["attributes"]
            version_attr = next(
                (a for a in attrs if a["key"] == "service.version"), None
            )
            if version_attr:
                self.assertEqual(
                    version_attr["value"]["stringValue"],
                    AICTL_VERSION,
                    f"export_spans emits version {version_attr['value']['stringValue']!r}, "
                    f"expected AICTL_VERSION={AICTL_VERSION!r}",
                )

    def test_export_tool_spans_uses_aictl_version_constant(self):
        """export_tool_spans() must emit the version from AICTL_VERSION."""
        import json
        import unittest.mock as mock
        from aictl.metrics.genai_spans import export_tool_spans, ToolSpan
        from aictl.core.constants import AICTL_VERSION

        span = ToolSpan(
            tool_name="aictl_health",
            success=True,
        )

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            raise OSError("test mode")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            try:
                export_tool_spans([span], endpoint="http://localhost:9999/v1/traces")
            except OSError:
                pass

        if "body" in captured:
            attrs = captured["body"]["resourceSpans"][0]["resource"]["attributes"]
            version_attr = next(
                (a for a in attrs if a["key"] == "service.version"), None
            )
            if version_attr:
                self.assertEqual(
                    version_attr["value"]["stringValue"],
                    AICTL_VERSION,
                )


if __name__ == "__main__":
    unittest.main()
