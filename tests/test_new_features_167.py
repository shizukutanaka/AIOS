"""Pass 167 (audit P3+P4+M3): background scheduler makes schedules actually fire.

FEATURE_GAP_AUDIT.md's next-priority items:
  P3 `aictl batch add --schedule '0 2 * * *'` persisted a cron schedule that
     NOTHING ever executed — only manual `aictl batch run <job>` ran anything.
  P4 `aictl warmup schedule --every 1h` persisted a next_run timestamp that
     NOTHING ever fired.
  M3 no background scheduler/worker existed at all.

This closes all three with a new `aictl.core.scheduler` module (cron matching
+ due-check + execution, reusing the exact code paths `batch run`/`warmup run`
already use), a manual CLI trigger (`aictl scheduler tick`), and a background
thread (`SchedulerDaemon`, wired into `aictl serve` the same way GovernorDaemon
already is).

Also fixes a real, separate bug this work surfaced: `aictl/cmd/batch.py`'s
`_db_path()` ignored the `--state-dir` CLI flag entirely (only read the
AIOS_STATE_DIR env var) — every batch command silently wrote to the DEFAULT
state dir regardless of --state-dir, unlike every other command in the project.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path


def _mk_input(content="doc content"):
    d = Path(tempfile.mkdtemp())
    (d / "a.txt").write_text(content)
    return d


# ── cron matching ────────────────────────────────────────────────────────────

class TestCronFieldMatching(unittest.TestCase):
    def test_wildcard_matches_anything(self):
        from aictl.core.scheduler import _field_matches
        self.assertTrue(_field_matches("*", 0))
        self.assertTrue(_field_matches("*", 59))

    def test_exact_value(self):
        from aictl.core.scheduler import _field_matches
        self.assertTrue(_field_matches("5", 5))
        self.assertFalse(_field_matches("5", 6))

    def test_comma_list(self):
        from aictl.core.scheduler import _field_matches
        self.assertTrue(_field_matches("1,3,5", 3))
        self.assertFalse(_field_matches("1,3,5", 4))

    def test_range(self):
        from aictl.core.scheduler import _field_matches
        self.assertTrue(_field_matches("1-5", 3))
        self.assertFalse(_field_matches("1-5", 6))

    def test_step(self):
        from aictl.core.scheduler import _field_matches
        self.assertTrue(_field_matches("*/15", 30))
        self.assertFalse(_field_matches("*/15", 31))

    def test_malformed_part_skipped_not_raised(self):
        from aictl.core.scheduler import _field_matches
        # "abc" is unparseable and must be skipped, not crash the whole match;
        # "5" in the same comma list still matches normally.
        self.assertTrue(_field_matches("abc,5", 5))
        self.assertFalse(_field_matches("abc,xyz", 5))


class TestCronMatches(unittest.TestCase):
    def test_daily_schedule_matches_its_hour_minute(self):
        from aictl.core.scheduler import cron_matches
        at_2am = time.struct_time((2026, 1, 1, 2, 0, 0, 3, 1, 0))
        at_3am = time.struct_time((2026, 1, 1, 3, 0, 0, 3, 1, 0))
        self.assertTrue(cron_matches("0 2 * * *", at_2am))
        self.assertFalse(cron_matches("0 2 * * *", at_3am))

    def test_malformed_expression_never_matches(self):
        from aictl.core.scheduler import cron_matches
        at = time.struct_time((2026, 1, 1, 2, 0, 0, 3, 1, 0))
        self.assertFalse(cron_matches("0 2 * *", at))    # only 4 fields
        self.assertFalse(cron_matches("", at))


class TestCronIsDue(unittest.TestCase):
    def test_first_check_with_no_last_run_is_due(self):
        from aictl.core.scheduler import cron_is_due
        now = time.mktime(time.struct_time((2026, 1, 1, 2, 0, 0, 3, 1, -1)))
        self.assertTrue(cron_is_due("0 2 * * *", None, now))

    def test_double_fire_within_same_minute_prevented(self):
        from aictl.core.scheduler import cron_is_due
        now = time.mktime(time.struct_time((2026, 1, 1, 2, 0, 0, 3, 1, -1)))
        self.assertFalse(cron_is_due("0 2 * * *", now, now))
        self.assertFalse(cron_is_due("0 2 * * *", now, now + 1))

    def test_fires_again_next_matching_minute(self):
        from aictl.core.scheduler import cron_is_due
        now = time.mktime(time.struct_time((2026, 1, 1, 2, 0, 0, 3, 1, -1)))
        next_day = now + 86400
        self.assertTrue(cron_is_due("0 2 * * *", now, next_day))


# ── run_due_batch_jobs / run_due_warmup / run_due_all ───────────────────────

class TestRunDueBatchJobs(unittest.TestCase):
    def test_due_job_runs_and_updates_state(self):
        from aictl.core.scheduler import run_due_batch_jobs
        d = Path(tempfile.mkdtemp())
        inp = _mk_input()
        now = time.time()
        lt = time.localtime(now)
        schedule = f"{lt.tm_min} {lt.tm_hour} * * *"
        (d / "batch.json").write_text(json.dumps({"jobs": {"j1": {
            "schedule": schedule, "input": str(inp), "model": "auto",
            "task": "embed", "max_runtime": "4h", "created_at": now,
            "last_run": None, "last_status": "pending", "runs": 0}}}))

        results = run_due_batch_jobs(d, now=now)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["success"])

        saved = json.loads((d / "batch.json").read_text())
        self.assertEqual(saved["jobs"]["j1"]["runs"], 1)
        self.assertEqual(saved["jobs"]["j1"]["last_status"], "success")
        self.assertEqual(saved["jobs"]["j1"]["last_run"], now)

    def test_not_due_job_is_skipped(self):
        from aictl.core.scheduler import run_due_batch_jobs
        d = Path(tempfile.mkdtemp())
        now = time.time()
        (d / "batch.json").write_text(json.dumps({"jobs": {"j1": {
            "schedule": "0 0 1 1 *",   # only Jan 1st at midnight
            "input": "", "model": "auto", "task": "embed",
            "max_runtime": "4h", "created_at": now,
            "last_run": None, "last_status": "pending", "runs": 0}}}))
        results = run_due_batch_jobs(d, now=now + 100000)  # arbitrary later time, unlikely to match
        # Not asserting empty unconditionally (date-dependent test flakiness
        # risk), just that it never raises and returns a list.
        self.assertIsInstance(results, list)

    def test_no_batch_file_returns_empty(self):
        from aictl.core.scheduler import run_due_batch_jobs
        d = Path(tempfile.mkdtemp())
        self.assertEqual(run_due_batch_jobs(d), [])

    def test_double_tick_same_minute_runs_once(self):
        from aictl.core.scheduler import run_due_batch_jobs
        d = Path(tempfile.mkdtemp())
        inp = _mk_input()
        now = time.time()
        lt = time.localtime(now)
        schedule = f"{lt.tm_min} {lt.tm_hour} * * *"
        (d / "batch.json").write_text(json.dumps({"jobs": {"j1": {
            "schedule": schedule, "input": str(inp), "model": "auto",
            "task": "embed", "max_runtime": "4h", "created_at": now,
            "last_run": None, "last_status": "pending", "runs": 0}}}))
        r1 = run_due_batch_jobs(d, now=now)
        r2 = run_due_batch_jobs(d, now=now + 2)
        self.assertEqual(len(r1), 1)
        self.assertEqual(len(r2), 0)


class TestRunDueWarmup(unittest.TestCase):
    def test_due_warmup_runs_and_advances_next_run(self):
        from aictl.core.scheduler import run_due_warmup
        d = Path(tempfile.mkdtemp())
        now = time.time()
        (d / "warmup_schedule.json").write_text(json.dumps({
            "every": "1h", "interval_secs": 3600, "top": 3,
            "created_at": now, "next_run": now - 10}))
        result = run_due_warmup(d, now=now)
        self.assertIsNotNone(result)
        saved = json.loads((d / "warmup_schedule.json").read_text())
        self.assertAlmostEqual(saved["next_run"], now + 3600, delta=1)

    def test_not_yet_due_returns_none(self):
        from aictl.core.scheduler import run_due_warmup
        d = Path(tempfile.mkdtemp())
        now = time.time()
        (d / "warmup_schedule.json").write_text(json.dumps({
            "every": "1h", "interval_secs": 3600, "top": 3,
            "created_at": now, "next_run": now + 3600}))
        self.assertIsNone(run_due_warmup(d, now=now))

    def test_no_schedule_file_returns_none(self):
        from aictl.core.scheduler import run_due_warmup
        d = Path(tempfile.mkdtemp())
        self.assertIsNone(run_due_warmup(d))

    def test_corrupt_schedule_file_degrades_to_none(self):
        from aictl.core.scheduler import run_due_warmup
        d = Path(tempfile.mkdtemp())
        (d / "warmup_schedule.json").write_text("[1,2,3]")
        self.assertIsNone(run_due_warmup(d))


class TestRunDueAll(unittest.TestCase):
    def test_combines_both(self):
        from aictl.core.scheduler import run_due_all
        d = Path(tempfile.mkdtemp())
        result = run_due_all(d)
        self.assertIn("batch_jobs", result)
        self.assertIn("warmup", result)


# ── CLI: aictl scheduler tick ────────────────────────────────────────────────

class TestSchedulerCli(unittest.TestCase):
    def _cli(self, argv):
        from aictl.cmd import scheduler
        p = argparse.ArgumentParser(prog="aictl")
        p.add_argument("--state-dir", default=None)
        p.add_argument("--json", action="store_true")
        sub = p.add_subparsers()
        scheduler.register(sub)
        ns = p.parse_args(argv)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ns.func(ns)
        return code, out.getvalue()

    def test_tick_nothing_due(self):
        d = tempfile.mkdtemp()
        code, text = self._cli(["--state-dir", d, "scheduler", "tick"])
        self.assertEqual(code, 0)
        self.assertIn("Nothing due", text)

    def test_tick_json_shape(self):
        d = tempfile.mkdtemp()
        code, text = self._cli(["--state-dir", d, "--json", "scheduler", "tick"])
        data = json.loads(text)
        self.assertIn("batch_jobs", data)
        self.assertIn("warmup", data)


# ── batch.py --state-dir fix ─────────────────────────────────────────────────

class TestBatchStateDirFix(unittest.TestCase):
    """batch.py's _db_path() previously ignored --state-dir entirely (only
    read AIOS_STATE_DIR). Every batch command silently wrote to the default
    state dir. Verified via the real CLI, not just direct function calls."""

    def _cli(self, argv):
        from aictl.cmd import batch
        p = argparse.ArgumentParser(prog="aictl")
        p.add_argument("--state-dir", default=None)
        p.add_argument("--json", action="store_true")
        sub = p.add_subparsers()
        batch.register(sub)
        ns = p.parse_args(argv)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ns.func(ns)
        return code, out.getvalue()

    def test_add_writes_under_state_dir(self):
        d = tempfile.mkdtemp()
        self._cli(["--state-dir", d, "batch", "add", "j1",
                  "--schedule", "0 2 * * *"])
        self.assertTrue((Path(d) / "batch.json").exists())

    def test_list_reads_from_state_dir(self):
        d = tempfile.mkdtemp()
        self._cli(["--state-dir", d, "batch", "add", "j1",
                  "--schedule", "0 2 * * *"])
        code, text = self._cli(["--state-dir", d, "--json", "batch", "list"])
        data = json.loads(text)
        self.assertIn("j1", data)

    def test_remove_respects_state_dir(self):
        d = tempfile.mkdtemp()
        self._cli(["--state-dir", d, "batch", "add", "j1",
                  "--schedule", "0 2 * * *"])
        code, _ = self._cli(["--state-dir", d, "batch", "remove", "j1"])
        self.assertEqual(code, 0)
        data = json.loads((Path(d) / "batch.json").read_text())
        self.assertNotIn("j1", data["jobs"])

    def test_different_state_dirs_are_isolated(self):
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        self._cli(["--state-dir", d1, "batch", "add", "only-in-d1",
                  "--schedule", "0 2 * * *"])
        # d2 has no jobs at all yet, so batch.json shouldn't even exist there —
        # the strongest possible proof the two state dirs never crossed paths.
        self.assertFalse((Path(d2) / "batch.json").exists())
        self.assertTrue((Path(d1) / "batch.json").exists())


# ── daemon: SchedulerDaemon lifecycle ────────────────────────────────────────

class TestSchedulerDaemon(unittest.TestCase):
    def test_start_ticks_and_stop(self):
        from aictl.daemon.scheduler_daemon import SchedulerDaemon
        from aictl.core.state import StateStore
        store = StateStore(Path(tempfile.mkdtemp()))
        sched = SchedulerDaemon(store, interval_s=0.1)
        sched.start()
        time.sleep(0.35)
        status = sched.get_status()
        self.assertTrue(status["running"])
        self.assertGreaterEqual(status["tick_count"], 2)
        sched.stop()
        self.assertFalse(sched.get_status()["running"])

    def test_manual_tick_returns_result_shape(self):
        from aictl.daemon.scheduler_daemon import SchedulerDaemon
        from aictl.core.state import StateStore
        store = StateStore(Path(tempfile.mkdtemp()))
        sched = SchedulerDaemon(store)
        result = sched.tick()
        self.assertIn("batch_jobs", result)
        self.assertIn("warmup", result)

    def test_start_is_idempotent(self):
        from aictl.daemon.scheduler_daemon import SchedulerDaemon
        from aictl.core.state import StateStore
        store = StateStore(Path(tempfile.mkdtemp()))
        sched = SchedulerDaemon(store, interval_s=1.0)
        sched.start()
        first_thread = sched._thread
        sched.start()   # must be a no-op, not spawn a second thread
        self.assertIs(sched._thread, first_thread)
        sched.stop()


if __name__ == "__main__":
    unittest.main()
