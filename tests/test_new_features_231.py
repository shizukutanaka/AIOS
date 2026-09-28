"""Pass 231: an engine using a different metric prefix read as healthy and idle.

Research for this pass (Zenn/Qiita ops articles, overseas monitoring guides)
agrees that Prometheus metrics are the backbone of production LLM serving --
and disagrees with itself about SGLang's prefix: `sglang:` in some, `sglang_`
in others. The adapter read only the underscore form, and `_prom_gauge` returns
0.0 for an absent metric, so an engine using the other prefix produced
TTFT = 0, queue = 0, active = 0: a perfect idle engine, with no signal anything
was unread. The governor acts on exactly these numbers.

Second defect, same block: `sglang_cache_hit_rate` was stored in
`kv_cache_utilization`. The router reads that field as "how full is the cache"
and flags `kv_cache_exhausted` above the SLO max, so a healthy 95% prefix-cache
hit rate marked the engine exhausted. Hit rate has its own field.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from aictl.runtime.adapters import SGLangAdapter

UNDERSCORE = """sglang_num_requests_waiting 7
sglang_num_requests_running 3
sglang_cache_hit_rate 0.95
sglang_time_to_first_token_seconds_sum 10.0
sglang_time_to_first_token_seconds_count 50
sglang_time_per_output_token_seconds_sum 1.0
sglang_time_per_output_token_seconds_count 100
"""
COLON = UNDERSCORE.replace("sglang_", "sglang:")


def _scrape(body: str):
    with patch("aictl.runtime.adapters._http_get", return_value=(200, body)):
        return SGLangAdapter("http://x").scrape_metrics()


class TestBothPrefixesAreRead(unittest.TestCase):
    def test_underscore_prefix(self):
        m = _scrape(UNDERSCORE)
        self.assertEqual((m.queue_depth, m.active_requests), (7, 3))

    def test_colon_prefix_reads_the_same_values(self):
        m = _scrape(COLON)
        self.assertEqual((m.queue_depth, m.active_requests), (7, 3))
        self.assertGreater(m.ttft_ms_p95, 0)

    def test_both_prefixes_agree(self):
        a, b = _scrape(UNDERSCORE), _scrape(COLON)
        self.assertEqual((a.ttft_ms_p95, a.itl_ms_p95, a.prefix_cache_hit_rate),
                         (b.ttft_ms_p95, b.itl_ms_p95, b.prefix_cache_hit_rate))


class TestAbsentIsNotZero(unittest.TestCase):
    def test_a_complete_scrape_reports_nothing_missing(self):
        self.assertEqual(_scrape(UNDERSCORE).missing_metrics, [])

    def test_unrecognised_names_are_reported_missing(self):
        m = _scrape("some_other_metric 1\n")
        self.assertEqual(len(m.missing_metrics), 4)

    def test_a_real_zero_is_not_reported_missing(self):
        m = _scrape(UNDERSCORE.replace("sglang_num_requests_waiting 7",
                                       "sglang_num_requests_waiting 0"))
        self.assertEqual(m.queue_depth, 0)
        self.assertNotIn("sglang_num_requests_waiting", m.missing_metrics)


class TestHitRateIsNotUtilization(unittest.TestCase):
    def test_hit_rate_lands_in_its_own_field(self):
        self.assertAlmostEqual(_scrape(UNDERSCORE).prefix_cache_hit_rate, 0.95)

    def test_a_high_hit_rate_is_not_read_as_a_full_cache(self):
        self.assertEqual(_scrape(UNDERSCORE).kv_cache_utilization, 0.0)

    def test_router_does_not_flag_a_healthy_cache_exhausted(self):
        from aictl.metrics.slo import SLOTarget

        m = _scrape(UNDERSCORE)
        self.assertLessEqual(m.kv_cache_utilization, SLOTarget().kv_cache_max)


if __name__ == "__main__":
    unittest.main()
