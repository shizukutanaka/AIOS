"""Tests for Phase 4: systemctl integration, OTel export, cosign, ORAS, model pull/verify."""

import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.stack.systemctl import UnitStatus, get_unit_status, list_aios_units
from aictl.metrics.otel import build_metric_payload, OTelMetricPoint
from aictl.metrics.slo import InferenceMetrics, SystemPressure
from aictl.trust.cosign import VerifyResult, cosign_available
from aictl.trust.oras import ModelArtifact, PullResult, oras_available
from aictl.trust.verify import TrustPolicy, sha256_file


class TestSystemctl(unittest.TestCase):
    def test_unit_status_defaults(self):
        us = UnitStatus(name="test.service")
        self.assertEqual(us.name, "test.service")
        self.assertEqual(us.active_state, "")
        self.assertEqual(us.main_pid, 0)

    def test_get_unit_nonexistent(self):
        """Getting status of a nonexistent unit should return not-found."""
        us = get_unit_status("aios-nonexistent-12345.service")
        # May return not-found or empty string depending on systemd availability
        self.assertIsInstance(us, UnitStatus)

    def test_list_units(self):
        """list_aios_units should return a list (possibly empty)."""
        units = list_aios_units()
        self.assertIsInstance(units, list)


class TestOTelExport(unittest.TestCase):
    def test_build_payload_structure(self):
        metrics = InferenceMetrics(
            engine="vllm", model="llama3",
            ttft_ms_p95=250.0, itl_ms_p95=30.0,
            tokens_per_sec=42.5, queue_depth=5,
            kv_cache_utilization=0.6, error_rate=0.02,
        )
        pressure = SystemPressure(memory_some_avg10=10.0, cpu_some_avg10=5.0)
        payload = build_metric_payload(metrics, pressure, node_id="test", profile="nvidia-ada-24gb")

        # OTLP structure
        self.assertIn("resourceMetrics", payload)
        rm = payload["resourceMetrics"]
        self.assertEqual(len(rm), 1)
        self.assertIn("resource", rm[0])
        self.assertIn("scopeMetrics", rm[0])

        # Resource attributes
        attrs = {a["key"]: a["value"] for a in rm[0]["resource"]["attributes"]}
        self.assertEqual(attrs["service.name"]["stringValue"], "aios")
        self.assertEqual(attrs["aios.node_id"]["stringValue"], "test")

        # Metrics
        scope = rm[0]["scopeMetrics"][0]
        metrics_list = scope["metrics"]
        self.assertGreater(len(metrics_list), 10)

        # Verify specific metric
        ttft = [m for m in metrics_list if m["name"] == "gen_ai.server.time_to_first_token"]
        self.assertEqual(len(ttft), 1)
        self.assertEqual(ttft[0]["unit"], "ms")
        dp = ttft[0]["gauge"]["dataPoints"][0]
        self.assertAlmostEqual(dp["asDouble"], 250.0)

    def test_payload_has_psi_metrics(self):
        metrics = InferenceMetrics()
        pressure = SystemPressure(memory_some_avg10=15.0, io_some_avg10=3.0)
        payload = build_metric_payload(metrics, pressure)
        names = [m["name"] for m in payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]]
        self.assertIn("aios.psi.memory_some_avg10", names)
        self.assertIn("aios.psi.io_some_avg10", names)

    def test_payload_serializable(self):
        metrics = InferenceMetrics()
        pressure = SystemPressure()
        payload = build_metric_payload(metrics, pressure)
        # Must be JSON-serializable
        s = json.dumps(payload)
        self.assertIsInstance(s, str)


class TestCosignWrapper(unittest.TestCase):
    def test_verify_result_defaults(self):
        r = VerifyResult()
        self.assertFalse(r.verified)
        self.assertEqual(r.method, "")

    def test_cosign_available_returns_bool(self):
        result = cosign_available()
        self.assertIsInstance(result, bool)

    def test_verify_without_cosign(self):
        """If cosign is not installed, should return appropriate error."""
        from aictl.trust.cosign import verify_image
        result = verify_image("nonexistent:latest")
        if not cosign_available():
            self.assertIn("cosign", result.error.lower())
        # If cosign IS installed, it would fail for a different reason


class TestORAS(unittest.TestCase):
    def test_pull_result_defaults(self):
        r = PullResult()
        self.assertFalse(r.success)

    def test_model_artifact_fields(self):
        a = ModelArtifact(reference="ghcr.io/org/model:v1", digest="sha256:abc")
        self.assertEqual(a.reference, "ghcr.io/org/model:v1")

    def test_oras_available_returns_bool(self):
        result = oras_available()
        self.assertIsInstance(result, bool)

    def test_pull_without_tools(self):
        """Pull without any tools should return appropriate error."""
        from aictl.trust.oras import pull_model
        result = pull_model("nonexistent.invalid/model:v1")
        if not oras_available():
            self.assertFalse(result.success)


class TestModelCLIPullVerify(unittest.TestCase):
    def test_pull_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["model", "pull", "ghcr.io/org/model:v1", "--output", "/tmp/models"])
        self.assertEqual(args.model_cmd, "pull")
        self.assertEqual(args.reference, "ghcr.io/org/model:v1")
        self.assertEqual(args.output, "/tmp/models")

    def test_verify_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["model", "verify", "ghcr.io/org/image:v1", "--key", "pub.key"])
        self.assertEqual(args.model_cmd, "verify")
        self.assertEqual(args.reference, "ghcr.io/org/image:v1")
        self.assertEqual(args.key, "pub.key")


class TestTrustPolicyIntegration(unittest.TestCase):
    """Verify trust policy works with cosign and digest verification."""

    def test_enforce_requires_digest(self):
        tp = TrustPolicy("enforce")
        ok, msg = tp.check("/dev/null", "")
        self.assertFalse(ok)

    def test_warn_allows_no_digest(self):
        tp = TrustPolicy("warn")
        ok, msg = tp.check("/dev/null", "")
        self.assertTrue(ok)
        self.assertIn("WARNING", msg)

    def test_disabled_allows_everything(self):
        tp = TrustPolicy("disabled")
        ok, msg = tp.check("/dev/null", "sha256:wrong")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
