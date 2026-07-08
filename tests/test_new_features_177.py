"""Pass 177 (IMPROVEMENTS.md item E): KV-budget hard filter in the router.

`SLOConfig.kv_cache_max` (default 0.9) was already threaded through
`check_slo()` (governor SLO-violation detection) and `cmd/optimize.py`'s
recommendation engine, but `runtime/router.py`'s `BrokerRouter` — the ONE
component that actually decides which endpoint gets the NEXT request —
never referenced it at all. The router's only KV-awareness was a soft
"headroom" factor (`1.0 - kv_cache_utilization`) folded into `_soft_score`,
which can still let a heavily-loaded, near-OOM engine win if its other score
components (cost, power) are favorable enough. This is the same
"documented control, ignored by the one component that should act on it"
class of gap fixed for trust_policy/guard_policy in earlier passes, applied
now to KV-budget routing.

Fix: a new hard-filter step in `BrokerRouter.route()`, applied right after
metrics are collected (metrics aren't available yet at the point
`_hard_filter` runs, so this can't live there) -- an engine whose
`kv_cache_utilization` exceeds the configured `slo_target.kv_cache_max` is
rejected outright with reason `kv_cache_exhausted (X% > Y%)`, the same way
`_hard_filter` already rejects unreachable/wrong-status/wrong-model
engines. If every candidate ends up rejected this way, `_fallback`'s
existing priority-order path still picks a reachable engine (degraded
service, not a hard outage) -- verified below, not assumed.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from aictl.metrics.slo import InferenceMetrics
from aictl.runtime.adapters import EngineHealth
from aictl.runtime.router import BrokerRouter, RouteRequest, SLOTarget


def _health(engine, endpoint="http://x", status="READY", reachable=True):
    return EngineHealth(engine=engine, endpoint=endpoint, reachable=reachable,
                        status=status)


def _metrics(engine, kv_cache_utilization=0.0):
    return InferenceMetrics(engine=engine, kv_cache_utilization=kv_cache_utilization)


def _route_with(healths, metrics_by_engine, slo_target=None):
    """Run BrokerRouter.route() with discover_engines/get_adapter mocked."""
    router = BrokerRouter(endpoints={h.engine: h.endpoint for h in healths},
                          slo_target=slo_target)

    class _Adapter:
        def __init__(self, engine):
            self._engine = engine

        def scrape_metrics(self):
            return metrics_by_engine.get(self._engine, InferenceMetrics(engine=self._engine))

    with patch("aictl.runtime.router.discover_engines", return_value=healths), \
         patch("aictl.runtime.router.get_adapter",
              side_effect=lambda engine, endpoint: _Adapter(engine)):
        return router.route(RouteRequest(model="test-model"))


class TestKvBudgetHardFilter(unittest.TestCase):
    def test_engine_over_kv_threshold_is_rejected_from_direct_selection(self):
        # With only one candidate and it KV-exhausted, the fallback safety
        # net (tested separately below) still returns it -- but it must be
        # marked fallback_used=True, proving it was NOT the router's clean/
        # eligible pick. reason_codes must record why it was rejected.
        healths = [_health("vllm")]
        metrics = {"vllm": _metrics("vllm", kv_cache_utilization=0.95)}
        decision = _route_with(healths, metrics, SLOTarget(kv_cache_max=0.9))
        self.assertTrue(any("kv_cache_exhausted" in c for c in decision.reason_codes))
        self.assertTrue(decision.fallback_used)

    def test_engine_under_kv_threshold_is_selected(self):
        healths = [_health("vllm")]
        metrics = {"vllm": _metrics("vllm", kv_cache_utilization=0.5)}
        decision = _route_with(healths, metrics, SLOTarget(kv_cache_max=0.9))
        self.assertEqual(decision.selected_engine, "vllm")

    def test_prefers_engine_under_threshold_over_exhausted_one(self):
        healths = [_health("vllm"), _health("sglang")]
        metrics = {
            "vllm": _metrics("vllm", kv_cache_utilization=0.98),
            "sglang": _metrics("sglang", kv_cache_utilization=0.4),
        }
        decision = _route_with(healths, metrics, SLOTarget(kv_cache_max=0.9))
        self.assertEqual(decision.selected_engine, "sglang")

    def test_exactly_at_threshold_is_not_rejected(self):
        # Reject strictly-greater-than, matching check_slo()'s own
        # `metrics.kv_cache_utilization > target.kv_cache_max` convention.
        healths = [_health("vllm")]
        metrics = {"vllm": _metrics("vllm", kv_cache_utilization=0.9)}
        decision = _route_with(healths, metrics, SLOTarget(kv_cache_max=0.9))
        self.assertEqual(decision.selected_engine, "vllm")

    def test_zero_kv_utilization_metric_does_not_falsely_reject(self):
        # kv_cache_utilization == 0.0 commonly means "not reported" (e.g.
        # Ollama/LMDeploy/LM Studio have no KV metric) -- must not be
        # mistaken for "0% used, therefore fine" vs falsely triggering on
        # unrelated zero-valued defaults; more importantly it must never be
        # treated as "exhausted" since 0.0 can never exceed a positive
        # threshold.
        healths = [_health("ollama")]
        metrics = {"ollama": _metrics("ollama", kv_cache_utilization=0.0)}
        decision = _route_with(healths, metrics, SLOTarget(kv_cache_max=0.9))
        self.assertEqual(decision.selected_engine, "ollama")

    def test_all_engines_exhausted_falls_back_to_reachable_priority_engine(self):
        # The safety-net claim: hard-rejecting every candidate must not
        # produce a hard outage -- _fallback's priority-order path still
        # picks a reachable engine.
        healths = [_health("vllm"), _health("ollama")]
        metrics = {
            "vllm": _metrics("vllm", kv_cache_utilization=0.99),
            "ollama": _metrics("ollama", kv_cache_utilization=0.99),
        }
        decision = _route_with(healths, metrics, SLOTarget(kv_cache_max=0.9))
        self.assertNotEqual(decision.selected_engine, "")
        self.assertTrue(decision.fallback_used)

    def test_default_slo_target_kv_cache_max_is_point_nine(self):
        router = BrokerRouter()
        self.assertEqual(router.slo_target.kv_cache_max, 0.9)

    def test_unreachable_engine_rejected_before_kv_check_ever_runs(self):
        # _hard_filter (reachability) must still short-circuit first --
        # metrics are never even collected for an unreachable engine.
        healths = [_health("vllm", reachable=False)]
        decision = _route_with(healths, {})
        self.assertTrue(any("unreachable" in c for c in decision.reason_codes))
        self.assertFalse(any("kv_cache_exhausted" in c for c in decision.reason_codes))


if __name__ == "__main__":
    unittest.main()
