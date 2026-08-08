"""Pass 192: vLLM KV prefix-cache offloading advice (OffloadingConnector).

`optimize_vllm_flags` has always emitted `--enable-prefix-caching`, but that
cache lives in whatever VRAM the weights left behind. On a large model / small
GPU the leftover is thin enough to thrash, so on prefix-heavy workloads
(multi-turn chat, shared RAG system prompts, agent loops) the reuse the flag
promises never materializes. vLLM's OffloadingConnector spills completed
blocks to pinned host memory so an evicted prefix is still a hit.

The emitted schema was verified against the connector's own pull request
(vllm-project/vllm#24498): kv_connector / kv_role /
kv_connector_extra_config.{block_size, cpu_bytes_to_use}, bytes as the unit,
replacing the legacy num_cpu_blocks. `spec_name` and multi-tier options appear
only in secondary sources, so they are deliberately not emitted — these tests
pin that boundary so a later change can't quietly start guessing.

The other thing pinned here: `cpu_bytes_to_use` is PINNED host memory —
unswappable, and gone from the OS while the engine runs. Over-allocating it
degrades the whole host, so the sizing bounds and the refuse-when-unknown
behavior are treated as safety properties, not preferences.
"""

from __future__ import annotations

import json
import unittest

from aictl.core.constants import (
    KV_OFFLOAD_HOST_RAM_FRACTION,
    KV_OFFLOAD_MIN_BYTES,
    KV_OFFLOAD_MIN_FREE_RAM_MB,
)
from aictl.runtime.kv_offload import (
    advise_kv_offload,
    build_kv_transfer_config,
    size_cpu_tier,
)
from aictl.runtime.optimize import HardwareProfile, optimize_vllm_flags


class TestTransferConfigSchema(unittest.TestCase):
    """Exact wire format, verified against vllm-project/vllm#24498."""

    def test_schema_matches_upstream_pr(self):
        payload = json.loads(build_kv_transfer_config(1_000_000_000))
        self.assertEqual(payload["kv_connector"], "OffloadingConnector")
        self.assertEqual(payload["kv_role"], "kv_both")
        extra = payload["kv_connector_extra_config"]
        self.assertEqual(extra["cpu_bytes_to_use"], 1_000_000_000)
        self.assertEqual(extra["block_size"], 64)

    def test_only_verified_keys_are_emitted(self):
        # spec_name / multi-tier keys were not confirmable against a primary
        # source; emitting a guessed key would silently produce a config the
        # engine may reject. Keep the surface to what was verified.
        payload = json.loads(build_kv_transfer_config(KV_OFFLOAD_MIN_BYTES))
        self.assertEqual(sorted(payload.keys()),
                         ["kv_connector", "kv_connector_extra_config", "kv_role"])
        self.assertEqual(sorted(payload["kv_connector_extra_config"].keys()),
                         ["block_size", "cpu_bytes_to_use"])

    def test_legacy_num_cpu_blocks_not_emitted(self):
        # cpu_bytes_to_use replaced it upstream; emitting both is contradictory.
        self.assertNotIn("num_cpu_blocks", build_kv_transfer_config(KV_OFFLOAD_MIN_BYTES))

    def test_config_is_compact_single_line(self):
        # It is embedded in a shell command; a newline would break the flag.
        rendered = build_kv_transfer_config(KV_OFFLOAD_MIN_BYTES)
        self.assertNotIn("\n", rendered)
        self.assertNotIn(", ", rendered)


class TestPinnedMemorySizing(unittest.TestCase):
    """cpu_bytes_to_use is unswappable host memory — sizing is a safety property."""

    def test_never_exceeds_configured_ram_fraction(self):
        host_mb = 256 * 1024
        got = size_cpu_tier(host_mb)
        self.assertLessEqual(got, int(host_mb * KV_OFFLOAD_HOST_RAM_FRACTION * 1024**2))

    def test_always_leaves_the_free_ram_floor(self):
        host_mb = 256 * 1024
        got_mb = size_cpu_tier(host_mb) / 1024**2
        self.assertGreaterEqual(host_mb - got_mb, KV_OFFLOAD_MIN_FREE_RAM_MB)

    def test_small_host_gets_nothing(self):
        # A host at/below the free-RAM floor must not have memory pinned at all.
        self.assertEqual(size_cpu_tier(KV_OFFLOAD_MIN_FREE_RAM_MB), 0)
        self.assertEqual(size_cpu_tier(4096), 0)

    def test_unknown_host_ram_returns_zero(self):
        self.assertEqual(size_cpu_tier(0), 0)
        self.assertEqual(size_cpu_tier(-1), 0)

    def test_result_is_zero_or_above_the_worthwhile_floor(self):
        # Never a token allocation: either meaningful or nothing.
        for host_mb in (0, 4096, 8192, 16384, 32768, 65536, 262144, 1048576):
            got = size_cpu_tier(host_mb)
            self.assertTrue(got == 0 or got >= KV_OFFLOAD_MIN_BYTES,
                            f"{host_mb}MB host produced {got} bytes")

    def test_tier_smaller_than_the_gpu_cache_it_backs_is_refused(self):
        # A CPU tier below the GPU tier adds a hop without extending the cache.
        self.assertEqual(size_cpu_tier(64 * 1024, gpu_kv_mb=100_000), 0)

    def test_monotonic_in_host_ram(self):
        sizes = [size_cpu_tier(mb) for mb in (32768, 65536, 131072, 262144)]
        self.assertEqual(sizes, sorted(sizes))


class TestAdviceDecisions(unittest.TestCase):
    def test_recommends_when_vram_is_tight_and_host_is_large(self):
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200)
        self.assertTrue(advice.recommended)
        self.assertGreaterEqual(advice.cpu_bytes, KV_OFFLOAD_MIN_BYTES)
        self.assertIn("--kv-transfer-config", advice.flag)

    def test_declines_when_gpu_cache_already_dwarfs_host_budget(self):
        # 80GB-class GPU running a small model: the GPU tier is already huge.
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=112_000)
        self.assertFalse(advice.recommended)
        self.assertEqual(advice.flag, "")

    def test_refuses_when_host_ram_unknown(self):
        advice = advise_kv_offload(host_ram_mb=0, gpu_kv_mb=8000)
        self.assertFalse(advice.recommended)
        self.assertIn("unknown", advice.reason)
        self.assertEqual(advice.cpu_bytes, 0)

    def test_declines_on_unsupported_vendor(self):
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=8000, vendor="cpu")
        self.assertFalse(advice.recommended)
        self.assertIn("cpu", advice.reason)

    def test_supported_vendors_accepted(self):
        for vendor in ("nvidia", "amd", "intel", "NVIDIA"):
            advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200,
                                       vendor=vendor)
            self.assertTrue(advice.recommended, vendor)

    def test_measured_low_reuse_vetoes_recommendation(self):
        # Offloading buys prefix hits and nothing else; measurement beats heuristic.
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200,
                                   prefix_reuse=0.02)
        self.assertFalse(advice.recommended)
        self.assertIn("prefix reuse", advice.reason)

    def test_measured_high_reuse_is_reported_in_notes(self):
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200,
                                   prefix_reuse=0.74)
        self.assertTrue(advice.recommended)
        self.assertTrue(any("74%" in n for n in advice.notes))

    def test_absent_measurement_states_the_assumption(self):
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200)
        joined = " ".join(advice.notes)
        self.assertIn("no measured prefix reuse", joined)
        self.assertIn("Single-shot prompts", joined)

    def test_notes_warn_that_memory_is_pinned(self):
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200)
        self.assertTrue(any("PINNED" in n for n in advice.notes))

    def test_notes_disclaim_the_common_misconception(self):
        # Offloading does not help a model that doesn't fit in VRAM.
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200)
        self.assertTrue(any("does not make a model that" in n for n in advice.notes))

    def test_declined_advice_always_explains_why(self):
        for kwargs in (
            {"host_ram_mb": 0, "gpu_kv_mb": 8000},
            {"host_ram_mb": 4096, "gpu_kv_mb": 8000},
            {"host_ram_mb": 128 * 1024, "gpu_kv_mb": 8000, "vendor": "cpu"},
            {"host_ram_mb": 128 * 1024, "gpu_kv_mb": 11_200, "prefix_reuse": 0.0},
        ):
            advice = advise_kv_offload(**kwargs)
            self.assertFalse(advice.recommended, kwargs)
            self.assertTrue(advice.reason.strip(), kwargs)

    def test_to_dict_is_json_serializable(self):
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200)
        payload = json.loads(json.dumps(advice.to_dict()))
        self.assertEqual(
            sorted(payload.keys()),
            ["cpu_bytes", "cpu_gib", "flag", "notes", "reason", "recommended"])


class TestOptimizeIntegration(unittest.TestCase):
    def _hw(self, **kw):
        base = dict(gpu_name="RTX 4090", gpu_count=1, vram_per_gpu_mb=24000,
                    compute_capability=89, host_ram_mb=128 * 1024)
        base.update(kw)
        return HardwareProfile(**base)

    def test_default_is_a_true_no_op(self):
        # The whole point: existing callers must see byte-identical output.
        hw = self._hw()
        off = optimize_vllm_flags("m", 8, hw)
        on_but_declined = optimize_vllm_flags("m", 8, self._hw(host_ram_mb=0),
                                              enable_kv_offload=True)
        self.assertFalse(any("kv-transfer-config" in f for f in off.flags))
        self.assertEqual(off.flags, on_but_declined.flags)

    def test_enabled_adds_only_the_offload_flag(self):
        hw = self._hw()
        off = optimize_vllm_flags("m", 8, hw)
        on = optimize_vllm_flags("m", 8, hw, enable_kv_offload=True)
        added = [f for f in on.flags if f not in off.flags]
        self.assertEqual(len(added), 1)
        self.assertIn("--kv-transfer-config", added[0])
        self.assertIn("OffloadingConnector", added[0])

    def test_flag_is_shell_quoted(self):
        # The JSON contains braces and quotes; unquoted it would be mangled.
        on = optimize_vllm_flags("m", 8, self._hw(), enable_kv_offload=True)
        flag = next(f for f in on.flags if "kv-transfer-config" in f)
        self.assertTrue(flag.startswith("--kv-transfer-config '"))
        self.assertTrue(flag.endswith("'"))

    def test_embedded_json_round_trips(self):
        on = optimize_vllm_flags("m", 8, self._hw(), enable_kv_offload=True)
        flag = next(f for f in on.flags if "kv-transfer-config" in f)
        payload = json.loads(flag.split("'", 1)[1].rsplit("'", 1)[0])
        self.assertEqual(payload["kv_connector"], "OffloadingConnector")
        self.assertGreater(payload["kv_connector_extra_config"]["cpu_bytes_to_use"], 0)

    def test_declining_is_explained_not_silent(self):
        on = optimize_vllm_flags("m", 8, self._hw(host_ram_mb=0), enable_kv_offload=True)
        self.assertTrue(any("not applied" in n for n in on.notes))

    def test_cpu_only_profile_declines(self):
        hw = HardwareProfile(vendor="cpu", host_ram_mb=64 * 1024)
        result = optimize_vllm_flags("m", 8, hw, enable_kv_offload=True)
        self.assertFalse(any("kv-transfer-config" in f for f in result.flags))

    def test_prefix_reuse_threaded_through_to_the_decision(self):
        hw = self._hw()
        high = optimize_vllm_flags("m", 8, hw, enable_kv_offload=True, prefix_reuse=0.8)
        low = optimize_vllm_flags("m", 8, hw, enable_kv_offload=True, prefix_reuse=0.01)
        self.assertTrue(any("kv-transfer-config" in f for f in high.flags))
        self.assertFalse(any("kv-transfer-config" in f for f in low.flags))

    def test_new_profile_fields_are_optional(self):
        # Existing call sites construct HardwareProfile without the new fields.
        hw = HardwareProfile(gpu_name="H100", vram_per_gpu_mb=80000)
        self.assertEqual(hw.host_ram_mb, 0)
        self.assertEqual(hw.vendor, "nvidia")
        self.assertTrue(optimize_vllm_flags("m", 8, hw).flags)


if __name__ == "__main__":
    unittest.main()
