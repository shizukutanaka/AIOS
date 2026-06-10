"""Tests for aictl guard stats subcommand."""

import argparse
import unittest


class TestGuardStatsRegistered(unittest.TestCase):
    """stats subcommand must be registered in guard.register()."""

    def _make_parser(self):
        from aictl.cmd.guard import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_stats_parser_exists(self):
        parser = self._make_parser()
        args = parser.parse_args(["guard", "stats"])
        self.assertEqual(args.func.__name__, "run_stats")


class TestGuardStatsLogic(unittest.TestCase):
    """run_stats() returns correct JSON from mocked perf data."""

    def _run(self, perf_summary):
        import os
        from unittest.mock import patch
        from aictl.cmd.guard import run_stats

        captured = []

        def fake_print_json(data):
            captured.append(data)

        args = argparse.Namespace(json=True)
        with patch("aictl.core.perf.summary", return_value=perf_summary), \
             patch("aictl.cmd.guard.print_json", side_effect=fake_print_json):
            ret = run_stats(args)
        return ret, captured[0] if captured else None

    def test_returns_zero(self):
        ret, _ = self._run({"guard": {"count": 10, "failures": 3,
                                      "p50_ms": 1.2, "p95_ms": 4.5}})
        self.assertEqual(ret, 0)

    def test_json_has_required_keys(self):
        _, data = self._run({"guard": {"count": 10, "failures": 3,
                                       "p50_ms": 1.2, "p95_ms": 4.5}})
        for key in ("total_scans", "clean", "blocks_or_pii",
                    "block_rate", "latency_p50_ms", "latency_p95_ms"):
            self.assertIn(key, data)

    def test_counts_are_correct(self):
        _, data = self._run({"guard": {"count": 20, "failures": 5,
                                       "p50_ms": 2.0, "p95_ms": 8.0}})
        self.assertEqual(data["total_scans"], 20)
        self.assertEqual(data["blocks_or_pii"], 5)
        self.assertEqual(data["clean"], 15)

    def test_block_rate_is_ratio(self):
        _, data = self._run({"guard": {"count": 10, "failures": 2,
                                       "p50_ms": 1.0, "p95_ms": 3.0}})
        self.assertAlmostEqual(data["block_rate"], 0.2, places=4)

    def test_zero_scans_gives_zero_block_rate(self):
        _, data = self._run({"guard": {"count": 0, "failures": 0,
                                       "p50_ms": 0.0, "p95_ms": 0.0}})
        self.assertEqual(data["block_rate"], 0.0)

    def test_no_guard_in_perf_gives_zeros(self):
        _, data = self._run({})
        self.assertEqual(data["total_scans"], 0)
        self.assertEqual(data["blocks_or_pii"], 0)

    def test_latency_in_output(self):
        _, data = self._run({"guard": {"count": 5, "failures": 0,
                                       "p50_ms": 1.5, "p95_ms": 9.9}})
        self.assertAlmostEqual(data["latency_p50_ms"], 1.5)
        self.assertAlmostEqual(data["latency_p95_ms"], 9.9)


if __name__ == "__main__":
    unittest.main()
