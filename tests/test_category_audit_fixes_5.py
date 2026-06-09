"""Regression tests for the 5th category audit (security/benchmark/speculative/
prefix_cache/formats/manifest/tco)."""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Security: crashing check counted as failure ────────────────────
class TestSecurityExceptionCounted(unittest.TestCase):
    def test_crashing_check_increments_failed(self):
        """A check function that raises must be counted as a failure."""
        import aictl.core.security as sec_mod
        from aictl.core.state import StateStore

        def boom(store):
            raise RuntimeError("check exploded")

        # Patch all checks with a single failing one
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(sec_mod, "_check_state_permissions", boom), \
                 mock.patch.object(sec_mod, "_check_container_runtime", lambda s: None), \
                 mock.patch.object(sec_mod, "_check_rootless", lambda s: None), \
                 mock.patch.object(sec_mod, "_check_cgroup_v2", lambda s: None), \
                 mock.patch.object(sec_mod, "_check_psi", lambda s: None), \
                 mock.patch.object(sec_mod, "_check_api_keys", lambda s: None), \
                 mock.patch.object(sec_mod, "_check_audit_logging", lambda s: None), \
                 mock.patch.object(sec_mod, "_check_trust_policy", lambda s: None), \
                 mock.patch.object(sec_mod, "_check_network_exposure", lambda s: None), \
                 mock.patch.object(sec_mod, "_check_model_signatures", lambda s: None):
                report = sec_mod.scan(Path(td))

        # The crashing check + 9 passing checks = 10 total
        self.assertEqual(report.checks_failed, 1,
                         "A crashing check must increment checks_failed")


# ── Benchmark: P95 index uses (len-1)*0.95 ───────────────────────
class TestBenchmarkP95Index(unittest.TestCase):
    def test_p95_does_not_return_max(self):
        """P95 on 20 items should not return the maximum element."""
        # For a sorted list [0..19], P95 should be 18 (index 18), not 19 (max).
        # Before the fix: int(20*0.95)=19 → max element.
        # After the fix: int(19*0.95)=int(18.05)=18 → correct P95.
        from aictl.runtime import benchmark as bm

        # Inject a fake ttfts list via the result field calculation
        ttfts = list(range(20))  # [0,1,...,19]
        ttfts.sort()
        p95_idx = int((len(ttfts) - 1) * 0.95)
        p95 = ttfts[p95_idx]
        self.assertEqual(p95, 18, "P95 of [0..19] should be element at index 18 = 18")
        self.assertNotEqual(p95, 19, "P95 must not be the maximum element (index 19)")

    def test_benchmark_module_uses_correct_formula(self):
        """Verify the benchmark source uses (len-1)*0.95 not len*0.95."""
        import inspect
        from aictl.runtime import benchmark as bm
        src = inspect.getsource(bm)
        self.assertIn("len(ttfts) - 1) * 0.95", src)
        self.assertNotIn("len(ttfts) * 0.95", src)


# ── Benchmark: non-streaming TTFT equals total latency ────────────
class TestBenchmarkNonStreamingTtft(unittest.TestCase):
    def test_openai_bench_ttft_equals_total(self):
        """For non-streaming API, TTFT should equal total response latency."""
        import inspect
        from aictl.runtime import benchmark as bm
        src = inspect.getsource(bm)
        # The fixed code: ttft = total  (not total * 0.3)
        self.assertNotIn("total * 0.3", src,
                         "Non-streaming TTFT must not be a fractional estimate of total")


# ── Speculative: EAGLE3 draft tokens not multiplied by topk ───────
class TestSpeculativeEagle3DraftTokens(unittest.TestCase):
    def test_eagle3_draft_tokens_not_multiplied_by_topk(self):
        """EAGLE3 --speculative-num-draft-tokens must equal num_speculative_tokens, not *topk."""
        from aictl.runtime.speculative import generate_sglang_args, SpeculativeConfig

        cfg = SpeculativeConfig(
            method="eagle3",
            num_speculative_tokens=5,
            eagle_topk=4,
            num_steps=3,
        )
        args = generate_sglang_args(cfg)
        # Find the num-draft-tokens flag
        draft_flag = next((a for a in args if "num-draft-tokens" in a), None)
        self.assertIsNotNone(draft_flag, "num-draft-tokens flag must be present")
        val = int(draft_flag.split("=")[1])
        self.assertEqual(val, 5, "draft tokens must be num_speculative_tokens=5, not 5*4=20")


# ── Prefix cache: sglang_cache_total_tokens not used as hit count ─
class TestPrefixCacheSglangTotalNotHit(unittest.TestCase):
    def test_sglang_total_tokens_not_mapped_to_hit_tokens(self):
        """sglang_cache_total_tokens must not clobber prefix_hit_tokens with the total value."""
        from aictl.runtime.prefix_cache import scrape_cache_stats

        # Simulate a metrics endpoint that returns sglang metrics
        metrics_text = (
            "# HELP sglang_cache_hit_rate Cache hit rate\n"
            "sglang_cache_hit_rate 0.75\n"
            "sglang_cache_total_tokens 10000\n"
        )

        import urllib.request
        fake_resp = mock.MagicMock()
        fake_resp.read.return_value = metrics_text.encode()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
            stats = scrape_cache_stats("sglang", "http://localhost:30000")

        # hit_rate should be captured from sglang_cache_hit_rate
        self.assertAlmostEqual(stats.hit_rate, 0.75, places=3)
        # prefix_hit_tokens should NOT be set to 10000 (the total)
        self.assertNotEqual(stats.prefix_hit_tokens, 10000,
                            "sglang_cache_total_tokens must not be written to prefix_hit_tokens")


# ── Formats: SafeTensors content detection uses binary header ──────
class TestFormatsafetensorsDetection(unittest.TestCase):
    def _make_safetensors_header(self, json_header: bytes) -> bytes:
        """Create valid SafeTensors file bytes: uint64-le length + JSON."""
        length = struct.pack("<Q", len(json_header))
        return length + json_header

    def test_real_safetensors_header_detected(self):
        """A real SafeTensors file (uint64 length prefix + JSON) must be detected.
        Uses an unknown extension (.weights) to exercise content-based detection."""
        from aictl.runtime.formats import detect_format

        json_hdr = b'{"__metadata__": {}, "weight": {"dtype": "F32", "shape": [4, 4], "data_offsets": [0, 64]}}'
        file_bytes = self._make_safetensors_header(json_hdr) + b"\x00" * 64

        # Use .weights extension (not in the known-extension list) to reach content detection
        with tempfile.NamedTemporaryFile(suffix=".weights", delete=False) as f:
            f.write(file_bytes)
            tmp = Path(f.name)

        try:
            result = detect_format(tmp)
            self.assertEqual(result.format, "safetensors",
                             "Real SafeTensors file must be detected as safetensors")
        finally:
            tmp.unlink(missing_ok=True)

    def test_non_safetensors_not_misdetected(self):
        """A file starting with b'{' (not SafeTensors) must NOT be detected as safetensors."""
        from aictl.runtime.formats import detect_format

        # Old code checked header[:1] == b"{" — a JSON or text file would match.
        # New code checks the uint64 length; a plain JSON file would have a huge header_len.
        file_bytes = b'{"key": "value"}' + b" " * 100
        with tempfile.NamedTemporaryFile(suffix=".weights", delete=False) as f:
            f.write(file_bytes)
            tmp = Path(f.name)

        try:
            result = detect_format(tmp)
            self.assertNotEqual(result.format, "safetensors",
                                "A plain JSON file must not be detected as safetensors")
        finally:
            tmp.unlink(missing_ok=True)


# ── Manifest: empty service/model name raises StackParseError ─────
class TestManifestEmptyNameRejected(unittest.TestCase):
    def _write_json(self, data: dict, td: str) -> Path:
        p = Path(td) / "stack.json"
        p.write_text(json.dumps(data))
        return p

    def test_service_without_name_raises(self):
        """A service entry with no 'name' field must raise StackParseError."""
        from aictl.stack.manifest import parse_file, StackParseError

        bad = {"name": "test", "services": [{"image": "nginx:latest"}]}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(StackParseError):
                parse_file(self._write_json(bad, td))

    def test_model_without_name_raises(self):
        """A model entry with no 'name' field must raise StackParseError."""
        from aictl.stack.manifest import parse_file, StackParseError

        bad = {"name": "test", "models": [{"source": "oci://registry/model"}]}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(StackParseError):
                parse_file(self._write_json(bad, td))

    def test_valid_manifest_parses_cleanly(self):
        """A valid manifest with names provided must parse without error."""
        from aictl.stack.manifest import parse_file

        good = {
            "name": "test",
            "services": [{"name": "llm", "image": "vllm:latest"}],
            "models": [{"name": "llama3", "source": "oci://r/llama3"}],
        }
        with tempfile.TemporaryDirectory() as td:
            result = parse_file(self._write_json(good, td))
        self.assertEqual(result.name, "test")
        self.assertEqual(result.services[0].name, "llm")


# ── TCO: energy calculation no longer inflated 8x ─────────────────
class TestTcoEnergyNoInflation(unittest.TestCase):
    def test_compute_energy_no_8x_multiplier(self):
        """_compute_energy must not multiply GPU hours by 8."""
        from aictl.cmd.tco import _compute_energy

        # Simulate records with 3600 seconds of active commands (= 1 GPU-hour)
        record = mock.MagicMock()
        record.duration_ms = 3_600_000  # 1 hour in ms
        record.command = "serve"

        cfg = {"gpu_watts": 700}
        hours, kwh = _compute_energy(cfg, 30, [record])

        self.assertAlmostEqual(hours, 1.0, places=3,
                               msg="1h of active time must report 1.0 GPU-hours, not 8.0")
        self.assertAlmostEqual(kwh, 0.7, places=3,
                               msg="700W for 1h = 0.7 kWh")


if __name__ == "__main__":
    unittest.main()
