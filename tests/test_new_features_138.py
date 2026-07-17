"""Pass 138: `tco forecast --days` window must be >= 1 (slice trap).

`run_forecast` selects its trend window with `sorted_dates[-window:]` where
`window = args.days`. Two failure modes in the V1/V3 family:

  --days 0  -> `[-0:]` == `[:]` -> the WHOLE history, silently treating "0 days"
               as "all days" (window_days jumped to every recorded day).
  --days -2 -> `[-(-2):]` == `[2:]` -> an arbitrary wrong subset (the
               negative-slice inversion), not "the last N days".

Pass 118's `_check_period_days` guarded `tco --period-days` (summary/carbon) but
not this separate `forecast --days` flag. Fix: `--days` uses type=positive_int
(reject 0 / negative at parse time, exit 2), and run_forecast floors the window
`max(1, ...)` as defense-in-depth for SDK callers building the Namespace
directly. Verified via real CLI with a 6-day seeded perf log.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch


def _seed_perf(days):
    d = tempfile.mkdtemp()
    with patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
        from aictl.core.perf import record, PerfRecord
        now = time.time()
        for i in range(days):
            record(PerfRecord(timestamp=now - i * 86400, command="infer",
                              duration_ms=100.0, exit_code=0, rss_mb_peak=10.0))
    return d


def _build_parser():
    from aictl.cmd import tco
    p = argparse.ArgumentParser(prog="aictl")
    sub = p.add_subparsers()
    tco.register(sub)
    return p


class TestForecastDaysParse(unittest.TestCase):
    def _expect_exit2(self, argv):
        parser = _build_parser()
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(argv)
        self.assertEqual(cm.exception.code, 2)

    def test_zero_rejected(self):
        self._expect_exit2(["tco", "forecast", "--days", "0"])

    def test_negative_rejected(self):
        self._expect_exit2(["tco", "forecast", "--days", "-2"])

    def test_positive_accepted(self):
        parser = _build_parser()
        ns = parser.parse_args(["tco", "forecast", "--days", "7"])
        self.assertEqual(ns.days, 7)


class TestForecastWindowFloor(unittest.TestCase):
    def _run_json(self, days, seed_days=6):
        d = _seed_perf(seed_days)
        from aictl.cmd import tco
        ns = argparse.Namespace(days=days, json=True)
        buf = io.StringIO()
        with patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
            with contextlib.redirect_stdout(buf):
                tco.run_forecast(ns)
        return json.loads(buf.getvalue())

    def test_window_zero_floored_not_all_history(self):
        # SDK/Namespace path: 0 must floor to a 1-day window, not the whole log.
        out = self._run_json(0, seed_days=6)
        self.assertEqual(out["window_days"], 1)

    def test_window_negative_floored(self):
        out = self._run_json(-3, seed_days=6)
        self.assertEqual(out["window_days"], 1)

    def test_valid_window_preserved(self):
        out = self._run_json(4, seed_days=6)
        self.assertEqual(out["window_days"], 4)


if __name__ == "__main__":
    unittest.main()
