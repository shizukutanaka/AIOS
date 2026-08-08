"""Pass 198: live fair-share admission (IMPROVEMENTS.md item M remainder).

Pass 190 shipped `core/fairness.py`, which reports Jain's index but decides
nothing. This is the decision half, plus an opt-in proxy gate.

**Not VTC.** The VTC paper (arXiv:2401.00588, OSDI '24) motivates this, but
its exact virtual-counter update — the input/output weighting and the precise
counter-lift rule — could not be verified: arxiv.org and every secondary
source carrying the formula are egress-blocked, the same wall Pass 190 hit.
Rather than ship a guessed formula under the paper's name, this implements the
two properties consistently reported across sources and textbook on their own:
least-service-first ordering, and a lift for new arrivals so identity rotation
cannot buy priority.

The design flaw these tests exist to prevent recurring: an earlier version
compared each entity against the *even share*. With N entities that ratio is
bounded above by N, so with two tenants a hog taking 98% of all tokens scored
1.96 and slipped under a threshold of 2.0 — the gate was a no-op in the
commonest multi-tenant case. Comparing against the least-served entity is
unbounded and expresses the actual intent.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from aictl.core.fair_scheduler import (
    DEFAULT_OUTPUT_WEIGHT,
    new_arrival_service,
    rank_by_service,
    should_admit,
    weighted_service,
)


@dataclass
class FakeBucket:
    entity_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class TestWeighting(unittest.TestCase):
    def test_output_tokens_cost_more_than_input(self):
        # Decode is sequential and holds KV cache; prefill is parallel.
        inp = weighted_service(FakeBucket("a", prompt_tokens=1000))
        out = weighted_service(FakeBucket("b", completion_tokens=1000))
        self.assertGreater(out, inp)
        self.assertAlmostEqual(out, 1000 * DEFAULT_OUTPUT_WEIGHT)

    def test_weight_is_configurable(self):
        bucket = FakeBucket("a", prompt_tokens=100, completion_tokens=100)
        self.assertAlmostEqual(weighted_service(bucket, 1.0), 200)
        self.assertAlmostEqual(weighted_service(bucket, 3.0), 400)

    def test_empty_bucket_is_zero(self):
        self.assertEqual(weighted_service(FakeBucket("a")), 0.0)


class TestTwoTenantCase(unittest.TestCase):
    """The regression that motivated the redesign: with N entities the
    share-ratio is bounded by N, so a 2-tenant hog could never exceed 2.0."""

    def setUp(self):
        self.buckets = [
            FakeBucket("hog", prompt_tokens=10_000, completion_tokens=40_000),
            FakeBucket("light", prompt_tokens=1_000, completion_tokens=500),
        ]

    def test_hog_is_deferred_with_only_two_tenants(self):
        decision = should_admit("hog", self.buckets)
        self.assertFalse(decision.admit)
        self.assertGreater(decision.ratio, 2.0)

    def test_light_tenant_is_always_admitted(self):
        self.assertTrue(should_admit("light", self.buckets).admit)

    def test_near_equal_tenants_are_both_admitted(self):
        # Fairness must not throttle tenants that are already balanced.
        even = [FakeBucket("a", prompt_tokens=50_000),
                FakeBucket("b", prompt_tokens=45_000)]
        self.assertTrue(should_admit("a", even).admit)
        self.assertTrue(should_admit("b", even).admit)


class TestStarvationFloor(unittest.TestCase):
    """A bucket sitting at zero must not defer every other tenant at once."""

    def setUp(self):
        self.buckets = [
            FakeBucket("idle"),                                   # 0 tokens
            FakeBucket("mid", prompt_tokens=500),
            FakeBucket("hog", prompt_tokens=100_000),
        ]

    def test_hog_still_deferred(self):
        self.assertFalse(should_admit("hog", self.buckets).admit)

    def test_mid_tier_is_not_stalled_behind_an_idle_account(self):
        # Without the floor, base=0 makes any nonzero usage infinitely over
        # the limit and the whole system halts behind one idle account.
        self.assertTrue(should_admit("mid", self.buckets).admit)

    def test_idle_entity_is_admitted(self):
        self.assertTrue(should_admit("idle", self.buckets).admit)


class TestNewArrivalLift(unittest.TestCase):
    def setUp(self):
        self.buckets = [
            FakeBucket("established", prompt_tokens=90_000),
            FakeBucket("light", prompt_tokens=2_000),
        ]

    def test_newcomer_is_credited_the_minimum_not_zero(self):
        # At zero a fresh identity would outrank everyone until it caught up,
        # so rotating API keys would reset priority.
        self.assertEqual(new_arrival_service(self.buckets), 2_000.0)

    def test_newcomer_is_admitted(self):
        self.assertTrue(should_admit("never-seen", self.buckets).admit)

    def test_no_buckets_means_no_credit(self):
        self.assertEqual(new_arrival_service([]), 0.0)

    def test_key_rotation_does_not_beat_an_established_light_user(self):
        # A newcomer starts level with the least-served, not ahead of it.
        newcomer = should_admit("fresh-key", self.buckets)
        light = should_admit("light", self.buckets)
        self.assertTrue(newcomer.admit)
        self.assertTrue(light.admit)


class TestFailsOpen(unittest.TestCase):
    """A fairness mechanism that denies service on missing data has traded a
    fairness problem for an availability problem, which is strictly worse."""

    def test_no_buckets_admits(self):
        self.assertTrue(should_admit("anyone", []).admit)

    def test_empty_entity_id_admits(self):
        self.assertTrue(should_admit("", [FakeBucket("a", prompt_tokens=1)]).admit)

    def test_single_entity_admits(self):
        decision = should_admit("solo", [FakeBucket("solo", prompt_tokens=99_999)])
        self.assertTrue(decision.admit)
        self.assertIn("single entity", decision.reason)

    def test_all_zero_usage_admits(self):
        buckets = [FakeBucket("a"), FakeBucket("b")]
        self.assertTrue(should_admit("a", buckets).admit)

    def test_every_decision_carries_a_reason(self):
        buckets = [FakeBucket("a", prompt_tokens=100_000), FakeBucket("b")]
        for entity in ("a", "b", "unknown", ""):
            self.assertTrue(should_admit(entity, buckets).reason.strip(), entity)


class TestRankingAndTunables(unittest.TestCase):
    def test_rank_is_least_served_first(self):
        buckets = [
            FakeBucket("big", prompt_tokens=9_000),
            FakeBucket("small", prompt_tokens=10),
            FakeBucket("mid", prompt_tokens=500),
        ]
        self.assertEqual(rank_by_service(buckets), ["small", "mid", "big"])

    def test_higher_yield_ratio_is_more_permissive(self):
        buckets = [FakeBucket("hog", prompt_tokens=10_000),
                   FakeBucket("light", prompt_tokens=1_000)]
        self.assertFalse(should_admit("hog", buckets, yield_ratio=2.0).admit)
        self.assertTrue(should_admit("hog", buckets, yield_ratio=100.0).admit)

    def test_decision_serializes(self):
        import json
        buckets = [FakeBucket("hog", prompt_tokens=100_000), FakeBucket("l", prompt_tokens=1)]
        payload = json.loads(json.dumps(should_admit("hog", buckets).to_dict()))
        self.assertEqual(sorted(payload.keys()),
                         ["admit", "entity_id", "fair_share", "ratio", "reason", "service"])


class TestConfigWiring(unittest.TestCase):
    def test_defaults_are_off(self):
        from aictl.core.config import Config
        config = Config()
        self.assertEqual(config.fair_share_policy, "off")
        self.assertEqual(config.fair_share_yield_ratio, 2.0)

    def _validated(self, **overrides):
        from aictl.cmd.config import _validate_config
        from aictl.core.config import Config
        config = Config()
        for key, value in overrides.items():
            setattr(config, key, value)
        return _validate_config(config)

    def test_invalid_policy_is_rejected(self):
        problems = self._validated(fair_share_policy="enfroce")   # typo
        self.assertTrue(any("fair_share_policy" in p for p in problems))

    def test_valid_policies_accepted(self):
        for policy in ("enforce", "warn", "off"):
            problems = self._validated(fair_share_policy=policy)
            self.assertFalse(any("fair_share_policy" in p for p in problems), policy)

    def test_yield_ratio_at_or_below_one_is_rejected(self):
        for bad in (1.0, 0.5, 0, -3):
            problems = self._validated(fair_share_yield_ratio=bad)
            self.assertTrue(any("fair_share_yield_ratio" in p for p in problems), bad)

    def test_non_numeric_yield_ratio_is_rejected(self):
        problems = self._validated(fair_share_yield_ratio="lots")
        self.assertTrue(any("fair_share_yield_ratio" in p for p in problems))

    def test_round_trips_through_dict_to_config(self):
        from aictl.cmd.config import _dict_to_config
        config = _dict_to_config({"fair_share_policy": "warn",
                                  "fair_share_yield_ratio": 3.5})
        self.assertEqual(config.fair_share_policy, "warn")
        self.assertAlmostEqual(config.fair_share_yield_ratio, 3.5)


class TestProxyGateShape(unittest.TestCase):
    def test_gate_runs_after_trust_and_guard(self):
        # daemon/CLAUDE.md pins trust -> guard ordering. Fair-share must come
        # after both: an unsafe or untrusted request should be refused on those
        # grounds regardless of whose quota it lands in.
        import inspect
        from aictl.daemon import proxy

        source = inspect.getsource(proxy)
        trust = source.index("allowed, reason = self._model_trust_ok(model)")
        guard = source.index("guard_ok, guard_reason = self._check_guard(body)")
        fair = source.index("fair_ok, fair_reason = self._check_fair_share(")
        self.assertLess(trust, guard, "trust gate must precede guard")
        self.assertLess(guard, fair, "fair-share gate must follow guard")

    def test_deferral_uses_a_retryable_status(self):
        # Being deferred is transient; 403 would imply a permission failure.
        import inspect
        from aictl.daemon import proxy

        source = inspect.getsource(proxy)
        idx = source.index("fair_ok, fair_reason")
        self.assertIn("503", source[idx:idx + 220])


if __name__ == "__main__":
    unittest.main()
