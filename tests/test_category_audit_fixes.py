"""Regression tests for the category-by-category audit fixes (v1.6).

Each test pins a previously-confirmed bug found by auditing the product by
category (core services, runtime, serving/integration).
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestCostRecommendation(unittest.TestCase):
    """cost.py: must not say 'on-prem (always cheaper)' when cloud is cheaper."""

    def test_cloud_cheaper_recommends_cloud(self):
        from aictl.core.cost import estimate_cost
        # High electricity + full utilization on an expensive-to-own GPU can make
        # cloud cheaper; assert the recommendation is never the inverted message.
        est = estimate_cost("RTX 4090", num_gpus=1, hours_per_day=24.0,
                            electricity_per_kwh=2.0)
        if est.cloud_monthly_usd <= est.onprem_monthly_usd:
            self.assertIn("cloud", est.recommendation)
            self.assertNotIn("always cheaper", est.recommendation)

    def test_recommendation_consistency(self):
        from aictl.core.cost import estimate_cost
        for hours in (1, 8, 24):
            for kwh in (0.05, 0.5, 2.0):
                est = estimate_cost("H100 SXM", hours_per_day=hours,
                                    electricity_per_kwh=kwh)
                # "always cheaper" may only appear when on-prem actually is.
                if "always cheaper" in est.recommendation:
                    self.assertLess(est.onprem_monthly_usd, est.cloud_monthly_usd)


class TestSecurityTrustPolicy(unittest.TestCase):
    """security.py: the trust-policy check must read the flat config key."""

    def test_disabled_trust_policy_detected(self):
        from aictl.core.state import StateStore
        from aictl.core.security import _check_trust_policy
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td))
            (store.dir / "config.json").write_text(
                json.dumps({"trust_policy": "disabled"}))
            finding = _check_trust_policy(store)
            self.assertIsNotNone(finding)
            self.assertEqual(finding.severity, "high")

    def test_warn_policy_no_finding(self):
        from aictl.core.state import StateStore
        from aictl.core.security import _check_trust_policy
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td))
            (store.dir / "config.json").write_text(
                json.dumps({"trust_policy": "warn"}))
            self.assertIsNone(_check_trust_policy(store))


class TestMeteringPerMinute(unittest.TestCase):
    """metering.py: per-minute token quota must actually be enforced."""

    def test_per_minute_quota_blocks(self):
        from aictl.core.metering import TokenMeter
        with tempfile.TemporaryDirectory() as td:
            meter = TokenMeter(state_dir=Path(td))
            meter.set_quota("k1", per_minute=100)
            self.assertTrue(meter.record("k1", "m", 40, 40))   # 80 ≤ 100
            self.assertFalse(meter.record("k1", "m", 30, 30))  # 80+60 > 100


class TestApiKeyTpm(unittest.TestCase):
    """apikeys.py: generate_key must accept a per-key TPM limit."""

    def test_generate_key_sets_tpm(self):
        from aictl.core.apikeys import KeyManager
        with tempfile.TemporaryDirectory() as td:
            mgr = KeyManager(state_dir=Path(td))
            _raw, key = mgr.generate_key("dev", rate_limit_rpm=10,
                                         rate_limit_tpm=5000)
            self.assertEqual(key.rate_limit_tpm, 5000)


class TestPrefixCacheNoSquaring(unittest.TestCase):
    """prefix_cache.py: sglang_cache_total_tokens is capacity, not hit count."""

    def test_sglang_total_tokens_not_squared(self):
        from aictl.runtime import prefix_cache
        # sglang_cache_total_tokens is cache *capacity*, not hit count.
        # It must NOT be mapped to prefix_hit_tokens (neither raw nor squared).
        # sglang_cache_hit_rate IS the correct hit-rate source for SGLang.
        metrics_text = "sglang_cache_hit_rate 0.75\nsglang_cache_total_tokens 10000\n"

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return metrics_text.encode()
        with mock.patch("urllib.request.urlopen", return_value=_Resp()):
            stats = prefix_cache.scrape_cache_stats("sglang", "http://x")
        # Capacity must not pollute prefix_hit_tokens (would be 10000 or 100_000_000)
        self.assertEqual(stats.prefix_hit_tokens, 0)
        # Hit rate is correctly captured from sglang_cache_hit_rate
        self.assertAlmostEqual(stats.hit_rate, 0.75)


class TestPrometheusLabelEscaping(unittest.TestCase):
    """prometheus.py: label values must be escaped."""

    def test_escape_label(self):
        from aictl.metrics.prometheus import _escape_label
        self.assertEqual(_escape_label('a"b'), 'a\\"b')
        self.assertEqual(_escape_label("a\\b"), "a\\\\b")
        self.assertEqual(_escape_label("a\nb"), "a\\nb")

    def test_gauge_with_quote_in_label_is_valid(self):
        from aictl.metrics.prometheus import _gauge
        lines: list[str] = []
        _gauge(lines, "x", "h", 1, {"host": 'we"ird'})
        metric_line = [l for l in lines if l.startswith("x{")][0]
        self.assertIn('host="we\\"ird"', metric_line)


class TestQuadletGpuValidation(unittest.TestCase):
    """quadlet.py: GPU env without device passthrough must be flagged."""

    def test_gpu_env_without_device_flagged(self):
        from aictl.stack.quadlet import validate_quadlet
        content = (
            "[Container]\n"
            "Image=vllm/vllm-openai:latest\n"
            "ContainerName=test\n"
            "Environment=NVIDIA_VISIBLE_DEVICES=all\n"
        )
        issues = validate_quadlet(content)
        self.assertTrue(any("AddDevice" in i for i in issues))

    def test_gpu_env_with_device_ok(self):
        from aictl.stack.quadlet import validate_quadlet
        content = (
            "[Container]\n"
            "Image=vllm/vllm-openai:latest\n"
            "ContainerName=test\n"
            "Environment=NVIDIA_VISIBLE_DEVICES=all\n"
            "AddDevice=nvidia.com/gpu=all\n"
        )
        issues = validate_quadlet(content)
        self.assertFalse(any("AddDevice" in i for i in issues))


class TestMcpToolsCallValidation(unittest.TestCase):
    """mcp_server.py: tools/call with no name is a JSON-RPC -32602 error."""

    def test_missing_name_is_invalid_params(self):
        from aictl.mcp_server import handle_request
        resp = handle_request({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                               "params": {"arguments": {}}})
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32602)


class TestSpeculativePEagleSglang(unittest.TestCase):
    """speculative.py: p-eagle must emit SGLang flags, not an empty list."""

    def test_p_eagle_sglang_non_empty(self):
        from aictl.runtime.speculative import generate_sglang_args, SpeculativeConfig
        args = generate_sglang_args(SpeculativeConfig(
            method="p-eagle",
            draft_model="amazon/GPT-OSS-20B-P-EAGLE",
        ))
        self.assertTrue(args)
        self.assertTrue(any("EAGLE3" in a for a in args))


class TestDynamoGpuCeil(unittest.TestCase):
    """dynamo.py: gpus_needed must be a true ceiling of the VRAM requirement."""

    def test_gpus_needed_is_ceil(self):
        from aictl.runtime.dynamo import estimate_dgdr_resources, DGDRSpec
        est = estimate_dgdr_resources(DGDRSpec(model="llama-3-70b", hardware="H100"))
        expected = min(math.ceil(est["total_vram_gb"] / 80), 8)
        self.assertEqual(est["gpus_needed"], expected)
        self.assertGreaterEqual(est["gpus_needed"], 1)


if __name__ == "__main__":
    unittest.main()
