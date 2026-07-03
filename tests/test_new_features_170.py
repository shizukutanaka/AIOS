"""Pass 170: self-audit finding — a non-positive `aictl warmup schedule --every`
interval was accepted with no validation, unlike its sibling `--top` flag
(which already rejects < 1). This is the exact bug class fixed extensively
elsewhere in this session (V1: a physical/scheduling quantity must be
positive) — except this time it was self-introduced in Pass 167's new
scheduler feature rather than pre-existing code.

Root cause: aictl/cmd/warmup.py's _parse_interval_secs('-1h') / ('0h')
returned -3600 / 0 with no rejection, and run_schedule persisted that value
straight into warmup_schedule.json's "interval_secs" field. When later
consumed by aictl/core/scheduler.py's run_due_warmup, `next_run = now +
interval_secs` computes a timestamp <= now for any non-positive
interval_secs, so `now < next_run` is false on every subsequent tick too —
the warmup would busy-fire on every single scheduler tick (every 60s via
SchedulerDaemon) forever instead of respecting the configured interval.

Fix (dual guard, matching the session's established pattern):
  1. CLI-level: run_schedule now rejects any --every that resolves to less
     than MIN_SCHEDULE_INTERVAL_SECS (60s), mirroring the existing
     `if top < 1: err(...); return 1` guard for --top.
  2. Library chokepoint: run_due_warmup floors interval_secs at
     MIN_SCHEDULE_INTERVAL_SECS as defense-in-depth, so a corrupted or
     hand-edited schedule file (bypassing the CLI entirely) still cannot
     produce a next_run <= now.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aictl.cmd.warmup import _parse_interval_secs, run_schedule
from aictl.core.constants import MIN_SCHEDULE_INTERVAL_SECS
from aictl.core.scheduler import run_due_warmup


class _NS:
    """Minimal argparse.Namespace stand-in."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestWarmupScheduleRejectsNonPositiveInterval(unittest.TestCase):
    def test_negative_every_is_rejected_at_cli(self):
        with tempfile.TemporaryDirectory() as d:
            rc = run_schedule(_NS(every="-1h", top=3, state_dir=d, json=False))
            self.assertEqual(rc, 1)
            self.assertFalse((Path(d) / "warmup_schedule.json").exists())

    def test_zero_every_is_rejected_at_cli(self):
        with tempfile.TemporaryDirectory() as d:
            rc = run_schedule(_NS(every="0h", top=3, state_dir=d, json=False))
            self.assertEqual(rc, 1)
            self.assertFalse((Path(d) / "warmup_schedule.json").exists())

    def test_sub_minimum_every_is_rejected_at_cli(self):
        # 30 raw seconds (no unit suffix -> multiplier defaults to 3600 in
        # _parse_interval_secs, so use a value that genuinely resolves low).
        with tempfile.TemporaryDirectory() as d:
            rc = run_schedule(_NS(every="0m", top=3, state_dir=d, json=False))
            self.assertEqual(rc, 1)

    def test_valid_interval_is_still_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            rc = run_schedule(_NS(every="1h", top=3, state_dir=d, json=False))
            self.assertEqual(rc, 0)
            path = Path(d) / "warmup_schedule.json"
            self.assertTrue(path.exists())
            schedule = json.loads(path.read_text())
            self.assertEqual(schedule["interval_secs"], 3600)


class TestParseIntervalSecsStillPermissiveAtParseLayer(unittest.TestCase):
    """_parse_interval_secs itself stays a pure best-effort parser (malformed
    strings fall back to the 1h default) — validation belongs at the
    run_schedule call site, not buried inside the parser."""

    def test_negative_and_zero_still_parse_without_raising(self):
        self.assertEqual(_parse_interval_secs("-1h"), -3600)
        self.assertEqual(_parse_interval_secs("0h"), 0)


class TestRunDueWarmupNeverBusyFiresOnBadInterval(unittest.TestCase):
    """Defense-in-depth: even if a schedule file bypasses the CLI (hand-edited,
    corrupted, or written by an older version), run_due_warmup must never let
    a non-positive interval_secs pin next_run <= now forever."""

    def _write_schedule(self, d: str, **overrides) -> Path:
        path = Path(d) / "warmup_schedule.json"
        schedule = {
            "every": "1h",
            "interval_secs": 3600,
            "top": 3,
            "created_at": 1_000_000.0,
            "next_run": 1_000_000.0,
        }
        schedule.update(overrides)
        path.write_text(json.dumps(schedule))
        return path

    def test_negative_interval_secs_advances_next_run_past_now(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_schedule(d, interval_secs=-3600, next_run=1_000_000.0)
            now = 1_000_000.0
            result = run_due_warmup(state_dir=d, now=now)
            self.assertIsNotNone(result)
            schedule = json.loads(path.read_text())
            self.assertGreater(schedule["next_run"], now)
            self.assertGreaterEqual(schedule["next_run"] - now, MIN_SCHEDULE_INTERVAL_SECS)

    def test_zero_interval_secs_advances_next_run_past_now(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_schedule(d, interval_secs=0, next_run=1_000_000.0)
            now = 1_000_000.0
            run_due_warmup(state_dir=d, now=now)
            schedule = json.loads(path.read_text())
            self.assertGreater(schedule["next_run"], now)

    def test_no_second_tick_immediately_fires_again(self):
        """The actual failure mode: with the bug, calling run_due_warmup twice
        in a row (simulating two scheduler ticks) would fire both times."""
        with tempfile.TemporaryDirectory() as d:
            self._write_schedule(d, interval_secs=-100, next_run=1_000_000.0)
            now = 1_000_000.0
            first = run_due_warmup(state_dir=d, now=now)
            self.assertIsNotNone(first)
            second = run_due_warmup(state_dir=d, now=now + 1)
            self.assertIsNone(second)

    def test_valid_interval_secs_is_unaffected(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_schedule(d, interval_secs=7200, next_run=1_000_000.0)
            now = 1_000_000.0
            run_due_warmup(state_dir=d, now=now)
            schedule = json.loads(path.read_text())
            self.assertEqual(schedule["next_run"], now + 7200)


if __name__ == "__main__":
    unittest.main()
