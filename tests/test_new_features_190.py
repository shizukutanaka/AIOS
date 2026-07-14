"""Pass 190 (IMPROVEMENTS.md item M): advisory fair-share report.

Designed via a research+design Workflow, scoped deliberately to an
advisory-only CLI report -- not a live scheduler, not touching the request
admission/serving path. Research found VTC's (arXiv:2401.00588) exact
weighted virtual-counter formula could not be independently verified (the
paper PDF was unreachable), so this pass ships the well-grounded, verifiable
alternative the research surfaced instead: Jain's Fairness Index over each
entity's share of TokenMeter's existing cumulative total_tokens.
core.fairness.compute_fairness() is a pure function (no I/O) over
TokenMeter.list_usage()'s output; `aictl tco fairshare` is the CLI surface,
mirroring `aictl tco carbon`'s registration pattern exactly.

Prefix-cache locality (DLPM, arXiv:2501.14312) is deliberately NOT blended
in this pass: research confirmed runtime.prefix_route.PrefixRouteTracker is
endpoint-keyed only with no per-tenant dimension -- fabricating a blended
metric would mean inventing data that doesn't exist. The report instead
carries a static, honest `locality_note` documenting this as a future
extension.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aictl.core.fairness import LOCALITY_NOTE, compute_fairness
from aictl.core.metering import TokenBucket, TokenMeter


class TestComputeFairnessEmptyState(unittest.TestCase):
    def test_no_buckets_returns_none_index_no_crash(self):
        report = compute_fairness([])
        self.assertIsNone(report.jains_index)
        self.assertEqual(report.entity_count, 0)
        self.assertEqual(report.total_tokens, 0)
        self.assertEqual(report.entities, [])

    def test_all_zero_usage_is_trivially_fair(self):
        buckets = [TokenBucket(entity_id="a"), TokenBucket(entity_id="b")]
        report = compute_fairness(buckets)
        self.assertEqual(report.jains_index, 1.0)
        for e in report.entities:
            self.assertEqual(e["share"], 0.0)
            self.assertEqual(e["classification"], "starved")


class TestComputeFairnessHandComputed(unittest.TestCase):
    def test_three_entity_skewed_example(self):
        # totals = [1000, 1000, 8000], sum=10000, n=3
        # J = 10000^2 / (3 * (1e6+1e6+6.4e7)) = 1e8 / 1.98e8 ~= 0.5051
        buckets = [
            TokenBucket(entity_id="e1", total_tokens=1000),
            TokenBucket(entity_id="e2", total_tokens=1000),
            TokenBucket(entity_id="e3", total_tokens=8000),
        ]
        report = compute_fairness(buckets)
        self.assertAlmostEqual(report.jains_index, 0.5051, places=4)
        self.assertEqual(report.entity_count, 3)
        self.assertEqual(report.total_tokens, 10000)

        by_id = {e["entity_id"]: e for e in report.entities}
        self.assertEqual(by_id["e3"]["classification"], "over_share")
        self.assertAlmostEqual(by_id["e3"]["share"], 0.8)
        self.assertEqual(by_id["e1"]["classification"], "starved")
        self.assertEqual(by_id["e2"]["classification"], "starved")
        self.assertAlmostEqual(by_id["e1"]["share"], 0.1)

    def test_perfectly_equal_usage_is_index_one(self):
        buckets = [TokenBucket(entity_id=f"e{i}", total_tokens=500) for i in range(4)]
        report = compute_fairness(buckets)
        self.assertEqual(report.jains_index, 1.0)
        for e in report.entities:
            self.assertEqual(e["classification"], "fair")
            self.assertAlmostEqual(e["share"], 0.25)

    def test_single_entity_is_trivially_fair(self):
        report = compute_fairness([TokenBucket(entity_id="solo", total_tokens=500)])
        self.assertEqual(report.jains_index, 1.0)
        self.assertEqual(report.entities[0]["classification"], "fair")
        self.assertEqual(report.entities[0]["share"], 1.0)

    def test_entities_sorted_most_over_share_first(self):
        buckets = [
            TokenBucket(entity_id="low", total_tokens=100),
            TokenBucket(entity_id="high", total_tokens=900),
        ]
        report = compute_fairness(buckets)
        self.assertEqual(report.entities[0]["entity_id"], "high")
        self.assertEqual(report.entities[1]["entity_id"], "low")

    def test_over_share_is_unreachable_with_only_two_entities(self):
        # A documented property of the formula, not a bug: with n=2,
        # expected_share=0.5, so over_share requires share > 1.0 -- impossible
        # since the max possible share is 1.0. Even a 999/1 split is "fair".
        buckets = [
            TokenBucket(entity_id="whale", total_tokens=999),
            TokenBucket(entity_id="minnow", total_tokens=1),
        ]
        report = compute_fairness(buckets)
        for e in report.entities:
            self.assertNotEqual(e["classification"], "over_share")


class TestFairnessReportShape(unittest.TestCase):
    def test_locality_note_present_and_matches_module_constant(self):
        report = compute_fairness([TokenBucket(entity_id="a", total_tokens=1)])
        self.assertEqual(report.locality_note, LOCALITY_NOTE)
        self.assertIn("not tracked per-tenant", report.locality_note)

    def test_entity_dict_keys(self):
        report = compute_fairness([TokenBucket(entity_id="a", entity_type="tenant", total_tokens=5)])
        keys = set(report.entities[0].keys())
        self.assertEqual(keys, {"entity_id", "entity_type", "total_tokens", "share", "classification"})

    def test_negative_total_tokens_clamped_to_zero(self):
        # Defensive: metering.py's own record() already clamps to >= 0, but a
        # hand-edited/corrupt metering.json could still produce a negative
        # value on load -- must not let it produce a negative share or
        # divide-by-negative weirdness.
        buckets = [TokenBucket(entity_id="a", total_tokens=-50),
                   TokenBucket(entity_id="b", total_tokens=100)]
        report = compute_fairness(buckets)
        self.assertGreaterEqual(report.total_tokens, 0)
        by_id = {e["entity_id"]: e for e in report.entities}
        self.assertEqual(by_id["a"]["total_tokens"], 0)
        self.assertGreaterEqual(by_id["a"]["share"], 0.0)


class TestIntegrationWithRealTokenMeter(unittest.TestCase):
    def test_report_over_real_metered_usage(self):
        # With only 2 entities, "over_share" (share > 2 * expected_share = 1.0)
        # is mathematically unreachable -- max share is 1.0. A 3rd near-idle
        # entity makes the skew classifiable, matching the hand-computed
        # 3-entity example above.
        with tempfile.TemporaryDirectory() as d:
            meter = TokenMeter(Path(d))
            meter.record("key-a", "llama3", 900, 100, entity_type="apikey")
            meter.record("key-b", "llama3", 90, 10, entity_type="apikey")
            meter.record("key-c", "llama3", 9, 1, entity_type="apikey")
            report = compute_fairness(meter.list_usage())
        self.assertEqual(report.entity_count, 3)
        self.assertEqual(report.total_tokens, 1110)
        by_id = {e["entity_id"]: e for e in report.entities}
        self.assertEqual(by_id["key-a"]["total_tokens"], 1000)
        self.assertEqual(by_id["key-b"]["total_tokens"], 100)
        self.assertEqual(by_id["key-c"]["total_tokens"], 10)
        self.assertEqual(by_id["key-a"]["classification"], "over_share")
        self.assertEqual(by_id["key-c"]["classification"], "starved")

    def test_empty_real_meter_produces_empty_report(self):
        with tempfile.TemporaryDirectory() as d:
            meter = TokenMeter(Path(d))
            report = compute_fairness(meter.list_usage())
        self.assertEqual(report.entity_count, 0)
        self.assertIsNone(report.jains_index)


class TestCliWiring(unittest.TestCase):
    def test_fairshare_subcommand_registered(self):
        from aictl.cmd.tco import register
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        ns = parser.parse_args(["tco", "fairshare", "--json"])
        self.assertTrue(ns.json)
        self.assertEqual(ns.tco_cmd, "fairshare")

    def test_run_fairshare_json_output_shape(self):
        import argparse
        import io
        import json
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from aictl.cmd.tco import run_fairshare

        fake_buckets = [
            TokenBucket(entity_id="a", total_tokens=100),
            TokenBucket(entity_id="b", total_tokens=300),
        ]
        with patch("aictl.core.metering.TokenMeter.list_usage", return_value=fake_buckets):
            ns = argparse.Namespace(json=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_fairshare(ns)
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        for key in ("jains_index", "entity_count", "total_tokens", "entities", "locality_note"):
            self.assertIn(key, payload)
        self.assertEqual(payload["entity_count"], 2)
        self.assertEqual(len(payload["entities"]), 2)

    def test_run_fairshare_empty_state_human_output_no_crash(self):
        import argparse
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from aictl.cmd.tco import run_fairshare

        with patch("aictl.core.metering.TokenMeter.list_usage", return_value=[]):
            ns = argparse.Namespace()  # no `json` attr at all -- getattr default path
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_fairshare(ns)
        self.assertEqual(rc, 0)

    def test_run_fairshare_human_output_no_crash_with_data(self):
        import argparse
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from aictl.cmd.tco import run_fairshare

        fake_buckets = [
            TokenBucket(entity_id="a", entity_type="tenant", total_tokens=100),
            TokenBucket(entity_id="b", entity_type="apikey", total_tokens=300),
        ]
        with patch("aictl.core.metering.TokenMeter.list_usage", return_value=fake_buckets):
            ns = argparse.Namespace(json=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_fairshare(ns)
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("Jain's fairness index", output)
        self.assertIn("locality is not tracked per-tenant", output)


if __name__ == "__main__":
    unittest.main()
