"""Pass 123 (loop): empty interval/duration string must not crash as a "bug".

Two sibling parsers read the trailing unit char with `s[-1]` inside a
`try/except (ValueError, KeyError)` — but an EMPTY string raises `IndexError`,
which neither caught. So `warmup schedule --every ""` and
`alert silence --duration ""` crashed out as:

    Unexpected error: IndexError: string index out of range
    ... If the problem persists, report at: github.com/.../issues/new

i.e. the user's own empty input was misdirected as a bug to file — the exact
anti-pattern errors.py warns against. Both parsers are designed to fall back to
a default for malformed input; they now include `IndexError` so an empty string
degrades to the default (1h) like any other unparseable value.
"""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path


class TestWarmupIntervalParse(unittest.TestCase):
    def test_empty_interval_falls_back_not_crash(self):
        from aictl.cmd.warmup import _parse_interval_secs
        self.assertEqual(_parse_interval_secs(""), 3600)   # was: IndexError

    def test_valid_units_still_parse(self):
        from aictl.cmd.warmup import _parse_interval_secs
        self.assertEqual(_parse_interval_secs("30m"), 1800)
        self.assertEqual(_parse_interval_secs("2h"), 7200)
        self.assertEqual(_parse_interval_secs("1d"), 86400)

    def test_malformed_falls_back(self):
        from aictl.cmd.warmup import _parse_interval_secs
        self.assertEqual(_parse_interval_secs("xyz"), 3600)

    def test_schedule_empty_every_succeeds(self):
        from aictl.cmd.warmup import run_schedule
        tmp = tempfile.mkdtemp()
        rc = run_schedule(argparse.Namespace(every="", top=3, state_dir=tmp,
                                             json=False))
        self.assertEqual(rc, 0)
        self.assertTrue((Path(tmp) / "warmup_schedule.json").exists())


class TestAlertDurationParse(unittest.TestCase):
    def test_silence_empty_duration_succeeds_not_crash(self):
        from aictl.cmd.alert import run_silence
        # Empty duration must not raise IndexError; the command records a
        # silence with the fallback window and returns success.
        rc = run_silence(argparse.Namespace(duration="", reason="", json=False))
        self.assertEqual(rc, 0)

    def test_silence_valid_duration_succeeds(self):
        from aictl.cmd.alert import run_silence
        rc = run_silence(argparse.Namespace(duration="2h", reason="maint",
                                            json=False))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
