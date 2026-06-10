"""Pass 56 regression tests: aiosd body-size cap, 503 on no engines, slo division guard."""

import io
import json
import unittest
from unittest.mock import MagicMock, patch


class TestAiosdReadBodySizeCap(unittest.TestCase):
    """_read_body must reject oversized requests with 413."""

    def _make_handler(self, content_length: int, body: bytes = b"{}"):
        from aictl.daemon.aiosd import AIOSHandler
        handler = AIOSHandler.__new__(AIOSHandler)
        handler.headers = {"Content-Length": str(content_length)}
        handler.rfile = io.BytesIO(body)
        handler._responses = []

        def fake_json_response(data, status=200):
            handler._responses.append((status, data))

        handler._json_response = fake_json_response
        return handler

    def test_normal_body_is_parsed(self):
        body = json.dumps({"key": "value"}).encode()
        handler = self._make_handler(len(body), body)
        result = handler._read_body()
        self.assertEqual(result, {"key": "value"})
        self.assertEqual(len(handler._responses), 0)

    def test_zero_length_returns_empty(self):
        handler = self._make_handler(0)
        result = handler._read_body()
        self.assertEqual(result, {})

    def test_body_at_limit_is_accepted(self):
        from aictl.core.constants import MAX_REQUEST_BODY
        body = b"x" * MAX_REQUEST_BODY
        # Make it valid JSON-ish — just test that no 413 is raised for exact limit
        body = json.dumps({"x": "a" * (MAX_REQUEST_BODY - 20)}).encode()
        length = len(body)
        if length <= MAX_REQUEST_BODY:
            handler = self._make_handler(length, body)
            result = handler._read_body()
            self.assertIsInstance(result, dict)
            self.assertEqual(len(handler._responses), 0)

    def test_oversized_body_returns_413(self):
        from aictl.core.constants import MAX_REQUEST_BODY
        oversized = MAX_REQUEST_BODY + 1
        handler = self._make_handler(oversized, b"x" * 10)
        with self.assertRaises((ValueError, Exception)):
            handler._read_body()
        self.assertEqual(len(handler._responses), 1)
        status, data = handler._responses[0]
        self.assertEqual(status, 413)
        self.assertIn("error", data)

    def test_max_request_body_constant_exists(self):
        from aictl.core.constants import MAX_REQUEST_BODY
        self.assertGreater(MAX_REQUEST_BODY, 0)
        self.assertLessEqual(MAX_REQUEST_BODY, 10 * 1024 * 1024)  # sanity: ≤ 10MB

    def test_max_request_body_is_1mb(self):
        from aictl.core.constants import MAX_REQUEST_BODY
        self.assertEqual(MAX_REQUEST_BODY, 1 * 1024 * 1024)


class TestAiosdBrokerFailover503(unittest.TestCase):
    """_broker_failover must return 503 when no healthy engine is available."""

    def _make_handler(self):
        from aictl.daemon.aiosd import AIOSHandler
        from aictl.core.state import StateStore
        import tempfile, pathlib

        handler = AIOSHandler.__new__(AIOSHandler)
        handler.headers = {"Content-Length": "0"}
        handler.rfile = io.BytesIO(b"")
        handler._responses = []
        tmpdir = tempfile.mkdtemp()
        handler.store = StateStore(pathlib.Path(tmpdir))

        def fake_json_response(data, status=200):
            handler._responses.append((status, data))

        handler._json_response = fake_json_response
        return handler

    def test_no_engines_returns_503(self):
        handler = self._make_handler()
        with patch("aictl.runtime.adapters.discover_engines", return_value=[]), \
             patch("aictl.core.config.load_config") as mock_cfg:
            mock_cfg.return_value.engines.to_dict.return_value = {}
            handler._broker_failover()
        self.assertEqual(len(handler._responses), 1)
        status, data = handler._responses[0]
        self.assertEqual(status, 503)
        self.assertIn("error", data)

    def test_healthy_engine_returns_200(self):
        handler = self._make_handler()
        mock_h = MagicMock()
        mock_h.reachable = True
        mock_h.status = "READY"
        mock_h.engine = "vllm"
        mock_h.endpoint = "http://localhost:8000/v1"

        with patch("aictl.runtime.adapters.discover_engines", return_value=[mock_h]), \
             patch("aictl.core.config.load_config") as mock_cfg:
            mock_cfg.return_value.engines.to_dict.return_value = {}
            handler._broker_failover()
        self.assertEqual(len(handler._responses), 1)
        status, data = handler._responses[0]
        self.assertEqual(status, 200)
        self.assertEqual(data["fallback_target"], "vllm")

    def test_degraded_engine_is_acceptable_fallback(self):
        handler = self._make_handler()
        mock_h = MagicMock()
        mock_h.reachable = True
        mock_h.status = "DEGRADED"
        mock_h.engine = "sglang"
        mock_h.endpoint = "http://localhost:30000/v1"

        with patch("aictl.runtime.adapters.discover_engines", return_value=[mock_h]), \
             patch("aictl.core.config.load_config") as mock_cfg:
            mock_cfg.return_value.engines.to_dict.return_value = {}
            handler._broker_failover()
        status, data = handler._responses[0]
        self.assertEqual(status, 200)
        self.assertTrue(data["degraded_mode"])


class TestSloGoodputDivisionGuard(unittest.TestCase):
    """compute_goodput must never raise ZeroDivisionError."""

    def test_empty_samples_returns_zero_ratio(self):
        from aictl.metrics.slo import compute_goodput, SLOTarget
        target = SLOTarget()
        result = compute_goodput([], target)
        self.assertEqual(result.goodput_ratio, 0.0)
        self.assertEqual(result.total_requests, 0)

    def test_all_pass_returns_1_0(self):
        from aictl.metrics.slo import compute_goodput, SLOTarget
        target = SLOTarget(ttft_p95_ms=500.0, itl_p95_ms=50.0)
        samples = [(100.0, 20.0), (200.0, 30.0)]
        result = compute_goodput(samples, target)
        self.assertAlmostEqual(result.goodput_ratio, 1.0)

    def test_all_fail_returns_zero(self):
        from aictl.metrics.slo import compute_goodput, SLOTarget
        target = SLOTarget(ttft_p95_ms=10.0, itl_p95_ms=5.0)
        samples = [(500.0, 100.0), (600.0, 200.0)]
        result = compute_goodput(samples, target)
        self.assertAlmostEqual(result.goodput_ratio, 0.0)

    def test_partial_pass(self):
        from aictl.metrics.slo import compute_goodput, SLOTarget
        target = SLOTarget(ttft_p95_ms=500.0, itl_p95_ms=50.0)
        samples = [(100.0, 20.0), (600.0, 100.0)]  # first passes, second fails
        result = compute_goodput(samples, target)
        self.assertAlmostEqual(result.goodput_ratio, 0.5)

    def test_goodput_rps_computed_when_window_set(self):
        from aictl.metrics.slo import compute_goodput, SLOTarget
        target = SLOTarget(ttft_p95_ms=500.0, itl_p95_ms=50.0)
        samples = [(100.0, 20.0), (200.0, 30.0)]
        result = compute_goodput(samples, target, window_seconds=10.0)
        self.assertAlmostEqual(result.goodput_rps, 0.2)  # 2/10

    def test_ratio_never_exceeds_1(self):
        from aictl.metrics.slo import compute_goodput, SLOTarget
        target = SLOTarget(ttft_p95_ms=9999.0, itl_p95_ms=9999.0)
        samples = [(1.0, 1.0)] * 100
        result = compute_goodput(samples, target)
        self.assertLessEqual(result.goodput_ratio, 1.0)


if __name__ == "__main__":
    unittest.main()
