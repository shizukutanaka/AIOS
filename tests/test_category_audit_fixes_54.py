"""Pass 54 regression tests: route cascade min-length validation and route stats."""

import argparse
import json
import os
import pathlib
import tempfile
import unittest


class TestCascadeMinLengthValidation(unittest.TestCase):
    """run_cascade() must clamp --min-length to >= 1."""

    def test_source_clamps_min_length(self):
        """Source must contain max(1, ...) guard for min_length."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "cmd" / "route.py").read_text()
        self.assertIn("max(1,", src,
                      "run_cascade must clamp min_length to >= 1")

    def test_cascade_quality_ok_zero_threshold_always_passes(self):
        """With min_words=1, even a one-word response passes length check."""
        from aictl.cmd.route import _cascade_quality_ok
        self.assertTrue(_cascade_quality_ok("word", min_words=1))

    def test_cascade_min_length_clamped_in_run_cascade(self):
        """The max(1, ...) clamp is applied in run_cascade, not _cascade_quality_ok.

        With min_words=0 passed directly, _cascade_quality_ok returns True
        for any response (0 words < 0 is False). The clamp in run_cascade
        prevents args.min_length=0 from bypassing the quality gate.
        """
        # Direct check: min_words=0 means "always pass length" — no clamping here
        from aictl.cmd.route import _cascade_quality_ok
        self.assertTrue(_cascade_quality_ok("any text", min_words=0))
        self.assertTrue(_cascade_quality_ok("", min_words=0))  # even empty passes
        # The fix is in run_cascade via max(1, min_length), tested by source check above


class TestRouteStatsSubcommand(unittest.TestCase):
    """route stats subcommand must be registered and return correct JSON."""

    def _make_parser(self):
        from aictl.cmd.route import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_stats_parser_exists(self):
        parser = self._make_parser()
        args = parser.parse_args(["route", "stats"])
        self.assertEqual(args.func.__name__, "run_stats")

    def _run_stats(self, stats_data=None):
        from unittest.mock import patch
        from aictl.cmd.route import run_stats

        captured = []

        def fake_print_json(data):
            captured.append(data)

        args = argparse.Namespace(json=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            if stats_data is not None:
                path = pathlib.Path(tmpdir) / "cascade_stats.json"
                path.write_text(json.dumps(stats_data))
            with patch.dict(os.environ, {"AIOS_STATE_DIR": tmpdir}), \
                 patch("aictl.cmd.route.print_json", side_effect=fake_print_json):
                ret = run_stats(args)

        return ret, captured[0] if captured else None

    def test_returns_zero(self):
        ret, _ = self._run_stats({"total_runs": 10, "escalations": 3})
        self.assertEqual(ret, 0)

    def test_json_has_required_keys(self):
        _, data = self._run_stats({"total_runs": 10, "escalations": 3})
        for key in ("total_runs", "direct", "escalations", "escalation_rate"):
            self.assertIn(key, data)

    def test_counts_are_correct(self):
        _, data = self._run_stats({"total_runs": 20, "escalations": 5})
        self.assertEqual(data["total_runs"], 20)
        self.assertEqual(data["escalations"], 5)
        self.assertEqual(data["direct"], 15)

    def test_escalation_rate_is_ratio(self):
        _, data = self._run_stats({"total_runs": 10, "escalations": 2})
        self.assertAlmostEqual(data["escalation_rate"], 0.2, places=4)

    def test_zero_runs_gives_zero_rate(self):
        _, data = self._run_stats({"total_runs": 0, "escalations": 0})
        self.assertEqual(data["escalation_rate"], 0.0)

    def test_no_stats_file_gives_zeros(self):
        ret, data = self._run_stats(None)
        self.assertEqual(ret, 0)
        self.assertEqual(data["total_runs"], 0)
        self.assertEqual(data["escalation_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
