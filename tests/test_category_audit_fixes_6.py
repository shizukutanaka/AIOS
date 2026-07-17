"""Pass 6 regression tests for correctness bugs identified by deep audit."""

import unittest
from unittest import mock


class TestFP8KVCacheMultiplier(unittest.TestCase):
    """optimize.py: FP8 KV cache compression must double available context budget."""

    def test_fp8_doubles_context_budget(self):
        from aictl.runtime.optimize import optimize_vllm_flags, HardwareProfile, OptimizeResult
        # Use tight VRAM so FP8 vs non-FP8 budget difference isn't swallowed by the 131072 cap.
        # 18 GB VRAM, 8B model @ FP16 = 16 GB weights → only ~2 GB left for KV.
        hw = HardwareProfile(
            gpu_name="RTX 4090",
            gpu_count=1,
            vram_per_gpu_mb=18432,  # 18 GB
            compute_capability=89,  # Ada — triggers fp8 kv (2x multiplier)
        )
        result = optimize_vllm_flags("meta-llama/Llama-3-8B", 8.0, hw)
        # Find the --max-model-len flag
        flags = result.flags
        context_flag = next((f for f in flags if f.startswith("--max-model-len=")), None)
        self.assertIsNotNone(context_flag)
        context_fp8 = int(context_flag.split("=")[1])

        # Repeat with CC=70 (no FP8 KV — multiplier 1.0, same VRAM)
        hw_nofp8 = HardwareProfile(
            gpu_name="RTX 3080",
            gpu_count=1,
            vram_per_gpu_mb=18432,
            compute_capability=70,
        )
        result2 = optimize_vllm_flags("meta-llama/Llama-3-8B", 8.0, hw_nofp8)
        context_flag2 = next((f for f in result2.flags if f.startswith("--max-model-len=")), None)
        self.assertIsNotNone(context_flag2)
        context_nofp8 = int(context_flag2.split("=")[1])

        # FP8 must yield MORE context, not less
        self.assertGreater(context_fp8, context_nofp8,
                           "FP8 KV cache (2x compression) should give more context, not less")


class TestFallbackStreamParam(unittest.TestCase):
    """fallback.py: stream parameter must not be silently ignored."""

    def test_stream_false_in_request_body(self):
        from aictl.runtime.fallback import cloud_completion, FallbackConfig

        sent_bodies = []

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"choices":[{"message":{"content":"ok"}}],"usage":{"prompt_tokens":5,"completion_tokens":3}}'

        def fake_urlopen(req, timeout=None):
            import json
            sent_bodies.append(json.loads(req.data))
            return _FakeResp()

        config = FallbackConfig(
            enabled=True,
            provider="openai",
            api_key="sk-test",
            model="gpt-4o-mini",
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_completion(config, [{"role": "user", "content": "hi"}], stream=False)

        self.assertEqual(len(sent_bodies), 1)
        self.assertFalse(sent_bodies[0]["stream"], "stream=False must be forwarded to provider")

    def test_stream_true_forwarded(self):
        from aictl.runtime.fallback import cloud_completion, FallbackConfig

        sent_bodies = []

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'

        def fake_urlopen(req, timeout=None):
            import json
            sent_bodies.append(json.loads(req.data))
            return _FakeResp()

        config = FallbackConfig(
            enabled=True,
            provider="openai",
            api_key="sk-test",
            model="gpt-4o-mini",
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            # stream=True is forwarded; response parsing may fail but the request is correct
            cloud_completion(config, [{"role": "user", "content": "hi"}], stream=True)

        self.assertEqual(len(sent_bodies), 1)
        self.assertTrue(sent_bodies[0]["stream"], "stream=True must be forwarded to provider")


class TestProxyEntityIdSlice(unittest.TestCase):
    """proxy.py: metering entity_id must use full API key, not truncated slice."""

    def test_full_key_used_for_entity_id(self):
        # Simulate what the proxy does for metering entity extraction
        auth = "Bearer aios-abc123def456ghi789jkl012mno345pqr678stu901"
        entity_id = "anonymous"
        if auth.startswith("Bearer ") and auth[7:].startswith("aios-"):
            entity_id = auth[7:]  # full key
        # Must be the full key, not a 13-char truncation
        self.assertEqual(entity_id, "aios-abc123def456ghi789jkl012mno345pqr678stu901")
        self.assertNotEqual(entity_id, auth[7:20], "Must not use truncated slice")

    def test_proxy_metering_uses_full_key(self):
        """Verify that the proxy source code uses auth[7:] not auth[7:20]."""
        import ast
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "daemon" / "proxy.py").read_text()
        # auth[7:20] truncates the key and must not appear in metering code
        self.assertNotIn("auth[7:20]", src,
                         "proxy.py must not truncate entity_id to 13 chars")
        # auth[7:] (full key) must be present
        self.assertIn("auth[7:]", src,
                      "proxy.py must use full key slice auth[7:] for entity_id")


class TestQuotaBarDeadCode(unittest.TestCase):
    """quota.py: progress bar expression must be removed (was dead code)."""

    def test_no_dead_bar_expression(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "quota.py").read_text()
        # The dead expression '█' * ... was never assigned to anything
        # After fix, it should be gone
        self.assertNotIn('"█" * min(20', src,
                         "Dead progress-bar expression should be removed from quota.py")


class TestInfoFileHandleClosed(unittest.TestCase):
    """info.py: test-count scan must close file handles (no resource leak)."""

    def test_no_unclosed_open_in_info(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "info.py").read_text()
        # The bare open(...) without context manager must be gone
        self.assertNotIn("ast.parse(open(", src,
                         "info.py must not call open() without a context manager")

    def test_info_count_runs_without_resource_warning(self):
        import warnings
        from aictl.cmd.info import _count_tests
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            count_str = _count_tests()
        resource_warnings = [x for x in w if issubclass(x.category, ResourceWarning)]
        self.assertEqual(len(resource_warnings), 0,
                         f"info.py _count_tests leaked file handles: {resource_warnings}")
        self.assertIsNotNone(count_str)


class TestNodesShouldPromote(unittest.TestCase):
    """nodes.py: should_promote_to_k3s must have no unreachable dead code."""

    def test_no_active_peers_returns_false(self):
        from aictl.runtime.nodes import NodeManager
        nm = NodeManager.__new__(NodeManager)
        nm.dir = None

        class _EmptyCluster:
            peers = []

        nm.load_cluster = lambda: _EmptyCluster()
        ok, reason = nm.should_promote_to_k3s()
        self.assertFalse(ok)
        self.assertIn("No active peers", reason)

    def test_one_active_peer_returns_true(self):
        from aictl.runtime.nodes import NodeManager

        class _Peer:
            status = "active"

        nm = NodeManager.__new__(NodeManager)
        nm.dir = None

        class _OneCluster:
            peers = [_Peer()]

        nm.load_cluster = lambda: _OneCluster()
        ok, reason = nm.should_promote_to_k3s()
        self.assertTrue(ok)
        self.assertIn("2 nodes", reason)

    def test_no_dead_return_after_always_true(self):
        """Verify the dead 'return False, Conditions not met' is gone."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "nodes.py").read_text()
        self.assertNotIn("Conditions not met", src,
                         "Dead unreachable return must be removed from nodes.py")


if __name__ == "__main__":
    unittest.main()
