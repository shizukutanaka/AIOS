"""Pass 82 (Socratic round 2) regression tests: global --json honored uniformly,
and empty-result JSON contract for meter/quota reports.

All three bugs below were found by running the real CLI and piping --json output
to a JSON parser — every one passed its mocked unit tests but emitted non-JSON
(or human text) in practice.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


class TestGlobalJsonHonored(unittest.TestCase):
    """`aictl --json <cmd>` must win even when the subcommand redefines --json.

    Regression: 32 subcommands define their own --json (default False). When the
    global form was used, the subparser's default clobbered the global True,
    silently producing human/non-JSON output. __main__.main() re-derives the
    flag from argv to honor it uniformly.

    Each test runs in an isolated, empty AIOS_STATE_DIR so it is deterministic
    (no shared perf/bus state from other tests) and exercises the empty-result
    JSON path — the exact case that was flaky before bench history was fixed.
    """

    def _run_main(self, argv: list[str]) -> tuple[int, str]:
        from aictl.__main__ import main
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as td, \
             patch.dict(os.environ, {"AIOS_STATE_DIR": td}), \
             patch.object(sys, "argv", argv), redirect_stdout(buf):
            rc = main()
        return rc, buf.getvalue()

    def test_global_json_events_list_is_valid_json(self):
        # events list defines its own --json; global form must still yield JSON.
        rc, out = self._run_main(["aictl", "--json", "events", "list"])
        self.assertEqual(rc, 0)
        parsed = json.loads(out)  # raises if not valid JSON
        self.assertIsInstance(parsed, list)

    def test_global_json_bench_history_empty_is_valid_json(self):
        # Empty perf store must yield exactly [] (the flaky case): the human
        # "No performance history" line must NOT be emitted in --json mode.
        rc, out = self._run_main(["aictl", "--json", "bench", "history"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])

    def test_subcommand_json_form_still_works(self):
        # The subcommand-local form must keep working too.
        rc, out = self._run_main(["aictl", "events", "list", "--json"])
        self.assertEqual(rc, 0)
        json.loads(out)

    def test_no_json_flag_stays_human(self):
        # Without --json anywhere, output must remain human (not JSON).
        rc, out = self._run_main(["aictl", "events", "list"])
        self.assertEqual(rc, 0)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out)


class TestMeterReportEmptyJson(unittest.TestCase):
    """meter report --json must emit [] when no usage is recorded, not human text."""

    def test_empty_report_json_is_list(self):
        from aictl.cmd.meter import run_report
        captured = []
        with patch("aictl.cmd.meter.TokenMeter") as MockMeter, \
             patch("aictl.cmd.meter.print_json", side_effect=captured.append):
            MockMeter.return_value.list_usage.return_value = []
            args = argparse.Namespace(json=True)
            ret = run_report(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0], [])

    def test_empty_report_human_unchanged(self):
        from aictl.cmd.meter import run_report
        buf = io.StringIO()
        with patch("aictl.cmd.meter.TokenMeter") as MockMeter, redirect_stdout(buf):
            MockMeter.return_value.list_usage.return_value = []
            args = argparse.Namespace(json=False)
            ret = run_report(args)
        self.assertEqual(ret, 0)
        self.assertIn("No usage recorded", buf.getvalue())


class TestQuotaReportEmptyJson(unittest.TestCase):
    """quota report --json must emit {} when no quotas exist, not empty output."""

    def test_empty_report_json_is_dict(self):
        from aictl.cmd.quota import run_report
        captured = []
        with patch("aictl.cmd.quota._load", return_value={"teams": {}}), \
             patch("aictl.cmd.quota.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True)
            ret = run_report(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0], {})

    def test_empty_report_human_unchanged(self):
        from aictl.cmd.quota import run_report
        with patch("aictl.cmd.quota._load", return_value={"teams": {}}), \
             patch("aictl.cmd.quota.warn") as mock_warn:
            args = argparse.Namespace(json=False)
            ret = run_report(args)
        self.assertEqual(ret, 0)
        mock_warn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
