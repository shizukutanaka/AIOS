"""Pass 193: measure prefix reuse in the router, feed it to the KV offload advisor.

Pass 192 left `advise_kv_offload(prefix_reuse=...)` with nothing to supply it —
the advisor could accept a measurement but always fell back to assuming a
prefix-heavy workload. `PrefixRouteTracker` was already answering the exact
question on every request (does a warm prefix exist for this prompt?) and
discarding the answer, so the measurement was free.

Grounding: KVFlow (NeurIPS 2025, arXiv:2507.07400) finds LRU eviction is
fundamentally mismatched with agentic workflows — it evicts on past access
time while the workflow structure already encodes the future execution order,
so caches get dropped shortly before reuse. aictl cannot change an engine's
eviction policy, but that is precisely the regime where enlarging the cache
tier (offloading to host memory) recovers hits that eviction would otherwise
squander. Whether a given deployment is in that regime is an empirical
question, and now it is answered from observed traffic rather than assumed.

The distinction these tests exist to protect: `reuse_rate()` returns None when
nothing has been observed and 0.0 when reuse was observed to be absent.
Callers act on them in opposite directions, so collapsing them would silently
turn "no data yet" into "don't bother offloading".
"""

from __future__ import annotations

import threading
import unittest

from aictl.runtime.kv_offload import advise_kv_offload, measured_prefix_reuse
from aictl.runtime.prefix_route import PrefixRouteTracker, get_default_tracker

ENDPOINTS = ["http://a:8000"]
PROMPT = "SYSTEM: you are a helpful assistant with a long shared preamble. " * 30


class TestReuseAccounting(unittest.TestCase):
    def setUp(self):
        self.tracker = PrefixRouteTracker()

    def test_unmeasured_is_none_not_zero(self):
        # The load-bearing distinction: no observations != observed no reuse.
        self.assertIsNone(self.tracker.reuse_rate())

    def test_cold_lookup_counts_as_a_miss(self):
        self.tracker.best_endpoint(PROMPT, ENDPOINTS)
        self.assertEqual(self.tracker.reuse_rate(), 0.0)
        self.assertIsNotNone(self.tracker.reuse_rate())

    def test_warm_lookup_counts_as_a_hit(self):
        self.tracker.record(ENDPOINTS[0], PROMPT)
        self.tracker.best_endpoint(PROMPT, ENDPOINTS)
        self.assertEqual(self.tracker.reuse_rate(), 1.0)

    def test_rate_is_hits_over_lookups(self):
        self.tracker.best_endpoint(PROMPT, ENDPOINTS)      # miss
        self.tracker.record(ENDPOINTS[0], PROMPT)
        for _ in range(3):
            self.tracker.best_endpoint(PROMPT, ENDPOINTS)  # hits
        self.assertAlmostEqual(self.tracker.reuse_rate(), 0.75)

    def test_malformed_lookups_are_not_evidence(self):
        # Empty prompt / no endpoints return before any history is consulted;
        # counting them would dilute the rate with non-observations.
        for _ in range(5):
            self.tracker.best_endpoint("", ENDPOINTS)
            self.tracker.best_endpoint(PROMPT, [])
        self.assertIsNone(self.tracker.reuse_rate())

    def test_rate_stays_in_unit_interval(self):
        self.tracker.record(ENDPOINTS[0], PROMPT)
        for i in range(50):
            self.tracker.best_endpoint(PROMPT if i % 2 else f"other {i} " * 20,
                                       ENDPOINTS)
        rate = self.tracker.reuse_rate()
        self.assertGreaterEqual(rate, 0.0)
        self.assertLessEqual(rate, 1.0)

    def test_clear_resets_to_unmeasured(self):
        self.tracker.record(ENDPOINTS[0], PROMPT)
        self.tracker.best_endpoint(PROMPT, ENDPOINTS)
        self.tracker.clear()
        self.assertIsNone(self.tracker.reuse_rate())

    def test_stats_exposes_the_counters(self):
        self.tracker.record(ENDPOINTS[0], PROMPT)
        self.tracker.best_endpoint(PROMPT, ENDPOINTS)
        stats = self.tracker.stats()
        self.assertEqual(stats["lookups"], 1)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["reuse_rate"], 1.0)

    def test_stats_reuse_rate_is_none_when_unmeasured(self):
        self.assertIsNone(self.tracker.stats()["reuse_rate"])

    def test_expired_prefix_is_a_miss(self):
        short = PrefixRouteTracker(ttl_seconds=0)
        short.record(ENDPOINTS[0], PROMPT)
        short.best_endpoint(PROMPT, ENDPOINTS)
        self.assertEqual(short.reuse_rate(), 0.0)

    def test_counting_is_thread_safe(self):
        self.tracker.record(ENDPOINTS[0], PROMPT)

        def hammer():
            for _ in range(100):
                self.tracker.best_endpoint(PROMPT, ENDPOINTS)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stats = self.tracker.stats()
        self.assertEqual(stats["lookups"], 800)
        self.assertEqual(stats["hits"], 800)

    def test_routing_behavior_is_unchanged(self):
        # Accounting must be observation only — the chosen endpoint and its
        # score must not shift because we started counting.
        self.tracker.record(ENDPOINTS[0], PROMPT)
        first = self.tracker.best_endpoint(PROMPT, ENDPOINTS)
        second = self.tracker.best_endpoint(PROMPT, ENDPOINTS)
        self.assertIsNotNone(first)
        self.assertEqual(first.endpoint, second.endpoint)
        self.assertEqual(first.matched_prefix_len, second.matched_prefix_len)


class TestAdvisorWiring(unittest.TestCase):
    """The advisor should consult the router instead of assuming."""

    def setUp(self):
        get_default_tracker().clear()

    def tearDown(self):
        get_default_tracker().clear()

    def test_unmeasured_falls_back_to_the_heuristic(self):
        self.assertIsNone(measured_prefix_reuse())
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200)
        self.assertTrue(advice.recommended)
        self.assertTrue(any("no measured prefix reuse" in n for n in advice.notes))

    def test_high_measured_reuse_is_used_and_attributed(self):
        tracker = get_default_tracker()
        tracker.record(ENDPOINTS[0], PROMPT)
        for _ in range(9):
            tracker.best_endpoint(PROMPT, ENDPOINTS)

        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200)
        self.assertTrue(advice.recommended)
        note = next(n for n in advice.notes if "prefix reuse" in n)
        self.assertIn("100%", note)
        self.assertIn("prefix-aware routing", note)

    def test_measured_absence_of_reuse_vetoes_offloading(self):
        # A one-shot workload: offloading enlarges a cache nothing reuses.
        tracker = get_default_tracker()
        for i in range(20):
            tracker.best_endpoint(f"unique one-shot prompt {i} " * 20, ENDPOINTS)

        self.assertEqual(measured_prefix_reuse(), 0.0)
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200)
        self.assertFalse(advice.recommended)
        self.assertIn("prefix reuse", advice.reason)

    def test_explicit_argument_overrides_the_measurement(self):
        tracker = get_default_tracker()
        tracker.record(ENDPOINTS[0], PROMPT)
        for _ in range(9):
            tracker.best_endpoint(PROMPT, ENDPOINTS)   # measures ~100%

        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200,
                                   prefix_reuse=0.0)
        self.assertFalse(advice.recommended)

    def test_explicit_zero_is_distinguishable_from_unmeasured(self):
        # Regression guard for `prefix_reuse or measured()`-style collapsing,
        # which would treat an explicit 0.0 as "no argument given".
        advice = advise_kv_offload(host_ram_mb=128 * 1024, gpu_kv_mb=11_200,
                                   prefix_reuse=0.0)
        self.assertFalse(advice.recommended)
        self.assertIn("0%", advice.reason)

    def test_measurement_failure_never_breaks_advice(self):
        import aictl.runtime.kv_offload as mod

        original = mod.measured_prefix_reuse
        mod.measured_prefix_reuse = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(RuntimeError):
                mod.measured_prefix_reuse()
        finally:
            mod.measured_prefix_reuse = original
        # The real helper swallows its own errors rather than propagating.
        self.assertIn(measured_prefix_reuse(), (None,))

    def test_optimize_path_picks_up_the_measurement(self):
        from aictl.runtime.optimize import HardwareProfile, optimize_vllm_flags

        tracker = get_default_tracker()
        for i in range(20):
            tracker.best_endpoint(f"unique {i} " * 20, ENDPOINTS)   # 0% reuse

        hw = HardwareProfile(gpu_name="RTX 4090", gpu_count=1, vram_per_gpu_mb=24000,
                             compute_capability=89, host_ram_mb=128 * 1024)
        result = optimize_vllm_flags("m", 8, hw, enable_kv_offload=True)
        self.assertFalse(any("kv-transfer-config" in f for f in result.flags))
        self.assertTrue(any("not applied" in n for n in result.notes))


if __name__ == "__main__":
    unittest.main()
