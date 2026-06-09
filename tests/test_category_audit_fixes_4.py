"""Regression tests for the 4th category audit (metrics/trust/runtime/MCP)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Metrics: prometheus dead-code removed ──────────────────────────
class TestPrometheusNoDeadCall(unittest.TestCase):
    def test_generate_metrics_text_no_unused_timestamp(self):
        """generate_metrics_text must not discard a bare int() call."""
        import inspect
        import aictl.metrics.prometheus as prom_mod
        src = inspect.getsource(prom_mod.generate_metrics_text)
        # The dead standalone `int(time.time() * 1000)` must be gone
        self.assertNotIn("int(time.time() * 1000)\n", src)


# ── Trust: cosign verify_attestation parses output ────────────────
class TestCosignAttestationParseOutput(unittest.TestCase):
    def test_verify_attestation_calls_parse_output(self):
        """verify_attestation must parse signer/issuer from cosign output."""
        import inspect
        import aictl.trust.cosign as cosign_mod
        src = inspect.getsource(cosign_mod.verify_attestation)
        # Must contain a call to _parse_cosign_output
        self.assertIn("_parse_cosign_output", src,
                      "verify_attestation must call _parse_cosign_output to populate signer/issuer")


# ── Trust: oras list_referrers handles list JSON ──────────────────
class TestOrasListReferrersJsonType(unittest.TestCase):
    def test_list_json_returns_empty_on_array_response(self):
        """list_referrers must not AttributeError when oras returns a JSON array."""
        import subprocess
        from aictl.trust import oras as oras_mod

        fake_proc = mock.MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = json.dumps([{"type": "unexpected"}])

        with mock.patch("subprocess.run", return_value=fake_proc):
            result = oras_mod.list_referrers("registry.example.com/model:latest")

        self.assertEqual(result, [], "list response (not dict) must return empty list, not raise")


# ── Runtime: fabric _detect_dram gb always defined ────────────────
class TestFabricDetectDramGbDefined(unittest.TestCase):
    def test_avail_before_total_does_not_raise(self):
        """_detect_dram must not raise UnboundLocalError if MemAvailable precedes MemTotal."""
        from aictl.runtime import fabric as fabric_mod

        # Simulate /proc/meminfo with MemAvailable before MemTotal
        fake_meminfo = "MemAvailable:  8000000 kB\nMemTotal: 16000000 kB\n"

        with mock.patch("builtins.open", mock.mock_open(read_data=fake_meminfo)):
            # Should not raise; may return None or a MemoryTier
            try:
                result = fabric_mod._detect_dram()
            except UnboundLocalError as e:
                self.fail(f"_detect_dram raised UnboundLocalError: {e}")


# ── Runtime: fabric dead sum removed ──────────────────────────────
class TestFabricDeadSumRemoved(unittest.TestCase):
    def test_generate_placement_policy_assigns_dram_gb(self):
        """generate_placement_policy must assign the dram sum to a variable (not discard it)."""
        import inspect
        from aictl.runtime import fabric as fabric_mod
        src = inspect.getsource(fabric_mod.generate_placement_policy)
        # The fixed version assigns the sum to dram_gb
        self.assertIn("dram_gb", src,
                      "dead sum() must be replaced with dram_gb = sum(...)")


# ── Runtime: health unknown status is not-healthy ─────────────────
class TestHealthUnknownStatusNotHealthy(unittest.TestCase):
    def test_unknown_container_status_marks_not_healthy(self):
        """An unknown health status string must not mark a container as healthy."""
        from aictl.runtime.health import check_container_health

        fake_result = mock.MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "running:weirdstatus"
        fake_result.stderr = ""

        with mock.patch("subprocess.run", return_value=fake_result):
            sh = check_container_health("mycontainer")

        self.assertFalse(sh.healthy,
                         "Unknown health status must result in healthy=False")


# ── Runtime: warmup KeyError safe access ─────────────────────────
class TestWarmupSafeDataAccess(unittest.TestCase):
    def test_get_warmup_candidates_missing_keys_no_crash(self):
        """get_warmup_candidates must not KeyError on records missing 'model' or 'engine'."""
        from aictl.runtime.warmup import WarmupManager

        mgr = WarmupManager.__new__(WarmupManager)

        # Usage record missing 'model' and 'engine' keys
        bad_data = {"count": 5, "last_used": 0, "avg_load_time_ms": 100}
        with mock.patch.object(mgr, "_load_usage", return_value={"k": bad_data}):
            try:
                candidates = mgr.get_warmup_candidates(top_n=3)
            except KeyError as e:
                self.fail(f"get_warmup_candidates raised KeyError: {e}")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].model, "")


# ── Runtime: warmup pull failure propagated ───────────────────────
class TestWarmupPullFailureReturned(unittest.TestCase):
    def test_failed_ollama_pull_returns_error_not_continues(self):
        """If ollama pull fails, _warmup_ollama must return an error, not silently continue."""
        import urllib.request
        from aictl.runtime.warmup import WarmupManager

        mgr = WarmupManager.__new__(WarmupManager)

        fake_tags = json.dumps({"models": []}).encode()
        fake_resp = mock.MagicMock()
        fake_resp.read.return_value = fake_tags
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = mock.MagicMock(return_value=False)

        failed_pull = mock.MagicMock()
        failed_pull.returncode = 1
        failed_pull.stderr = b"model not found"

        with mock.patch("urllib.request.urlopen", return_value=fake_resp), \
             mock.patch("subprocess.run", return_value=failed_pull):
            result = mgr._warmup_ollama("nonexistent-model:999b")

        self.assertEqual(result["status"], "error",
                         "Failed pull must set status='error'")
        self.assertIn("error", result,
                      "Failed pull must populate result['error']")


# ── MCP: _tool_optimize validates required params ─────────────────
class TestMcpOptimizeRequiredParams(unittest.TestCase):
    def test_missing_model_returns_error_not_key_error(self):
        """_tool_optimize must return isError response when 'model' is missing."""
        from aictl.mcp_server import _tool_optimize

        result = _tool_optimize({"model_size_b": 7, "gpu": "H100"})
        self.assertTrue(result.get("isError"), "Missing 'model' must return isError response")

    def test_missing_model_size_b_returns_error(self):
        """_tool_optimize must return isError response when 'model_size_b' is missing."""
        from aictl.mcp_server import _tool_optimize

        result = _tool_optimize({"model": "llama3:8b", "gpu": "H100"})
        self.assertTrue(result.get("isError"), "Missing 'model_size_b' must return isError response")

    def test_valid_params_succeed(self):
        """_tool_optimize with valid params must not return isError."""
        from aictl.mcp_server import _tool_optimize

        result = _tool_optimize({"model": "llama3:8b", "model_size_b": 8, "gpu": "H100"})
        self.assertFalse(result.get("isError", False))
        self.assertIn("content", result)


if __name__ == "__main__":
    unittest.main()
