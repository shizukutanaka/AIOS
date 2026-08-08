"""Pass 195: persist prefix reuse so short-lived CLI runs see a real measurement.

Item W closed with a documented limitation: the reuse rate was process-local,
so `aictl deploy optimize --kv-offload` — a fresh process that has served no
traffic — always fell back to the heuristic. The measurement only ever
accumulated in the long-lived daemon that does the routing, which is precisely
the process that never asks for the advice.

The log stores *deltas*, not absolute counts. Appends under PIPE_BUF are
atomic on POSIX, so concurrent writers share one file without locking and a
reader simply sums; absolute counts would need read-modify-write and would
race. This mirrors `core/perf.py`'s rationale for jsonl over a mutable file.

Properties these tests protect:
  * flushing twice must not double-count (deltas advance by what was written)
  * a crashed writer's truncated final line must not discard valid records
  * truncation collapses history into a summary rather than dropping it, so
    trimming cannot distort the rate
  * None still means unmeasured, never 0.0
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from aictl.runtime.prefix_route import (
    PrefixRouteTracker,
    _reuse_log_path,
    persisted_reuse_rate,
    truncate_reuse_log,
)

def _now() -> int:
    import time
    return int(time.time())


ENDPOINTS = ["http://a:8000"]
SHARED = "SHARED SYSTEM PREAMBLE, identical across requests. " * 30


class _IsolatedState(unittest.TestCase):
    """Each test gets its own state dir so the log never leaks between tests."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("AICTL_STATE_DIR")
        os.environ["AICTL_STATE_DIR"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AICTL_STATE_DIR", None)
        else:
            os.environ["AICTL_STATE_DIR"] = self._prev
        self._tmp.cleanup()

    def _warm_tracker(self, hits=9, misses=1):
        tracker = PrefixRouteTracker()
        for i in range(misses):
            tracker.best_endpoint(f"cold prompt {i} " * 30, ENDPOINTS)
        tracker.record(ENDPOINTS[0], SHARED)
        for _ in range(hits):
            tracker.best_endpoint(SHARED, ENDPOINTS)
        return tracker


class TestPersistence(_IsolatedState):
    def test_absent_log_is_unmeasured_not_zero(self):
        self.assertIsNone(persisted_reuse_rate())

    def test_flush_writes_the_rate(self):
        tracker = self._warm_tracker(hits=9, misses=1)
        self.assertTrue(tracker.flush_reuse())
        self.assertAlmostEqual(persisted_reuse_rate(), 0.9)

    def test_reflush_without_new_lookups_is_a_noop(self):
        tracker = self._warm_tracker()
        tracker.flush_reuse()
        first = persisted_reuse_rate()
        self.assertFalse(tracker.flush_reuse())
        self.assertEqual(persisted_reuse_rate(), first)

    def test_deltas_accumulate_across_trackers(self):
        # Stands in for two processes writing the same log.
        a = self._warm_tracker(hits=9, misses=1)     # 9/10
        a.flush_reuse()
        b = PrefixRouteTracker()
        for i in range(10):                          # 0/10
            b.best_endpoint(f"unique {i} " * 30, ENDPOINTS)
        b.flush_reuse()
        self.assertAlmostEqual(persisted_reuse_rate(), 9 / 20)

    def test_incremental_flushes_do_not_double_count(self):
        tracker = self._warm_tracker(hits=9, misses=1)
        tracker.flush_reuse()
        for _ in range(10):
            tracker.best_endpoint(SHARED, ENDPOINTS)
        tracker.flush_reuse()
        # 19 hits of 20 lookups total, counted once each.
        self.assertAlmostEqual(persisted_reuse_rate(), 19 / 20)

    def test_clear_resets_the_flush_cursor_too(self):
        # Regression: clear() reset the counters but not the flush cursors, so
        # `lookups - flushed` went negative and a cleared tracker silently
        # under-persisted (or persisted nothing) until it passed the stale
        # cursor. Found by a shutdown-drain test recording 5 of 10 lookups.
        tracker = self._warm_tracker(hits=4, misses=1)     # 5 lookups
        self.assertTrue(tracker.flush_reuse())
        tracker.clear()

        tracker.record(ENDPOINTS[0], SHARED)
        for _ in range(3):
            tracker.best_endpoint(SHARED, ENDPOINTS)
        self.assertTrue(tracker.flush_reuse(), "cleared tracker refused to flush")

        with open(_reuse_log_path(), encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        # 5 from before the clear, 3 after — every lookup accounted for once.
        self.assertEqual(sum(r["lookups"] for r in records), 8)

    def test_empty_log_is_unmeasured(self):
        _reuse_log_path().parent.mkdir(parents=True, exist_ok=True)
        _reuse_log_path().write_text("")
        self.assertIsNone(persisted_reuse_rate())

    def test_zero_lookup_records_are_unmeasured(self):
        _reuse_log_path().parent.mkdir(parents=True, exist_ok=True)
        _reuse_log_path().write_text(
            json.dumps({"lookups": 0, "hits": 0, "ts": _now()}) + "\n")
        self.assertIsNone(persisted_reuse_rate())

    def test_rate_stays_in_unit_interval(self):
        _reuse_log_path().parent.mkdir(parents=True, exist_ok=True)
        # Defensive: a corrupt record claiming more hits than lookups must not
        # produce a rate above 1.0 that downstream logic would misread.
        _reuse_log_path().write_text(
            json.dumps({"lookups": 10, "hits": 9999, "ts": _now()}) + "\n")
        self.assertLessEqual(persisted_reuse_rate(), 1.0)


class TestCorruptionResilience(_IsolatedState):
    def test_truncated_final_line_does_not_discard_valid_records(self):
        tracker = self._warm_tracker(hits=9, misses=1)
        tracker.flush_reuse()
        with open(_reuse_log_path(), "a", encoding="utf-8") as fh:
            fh.write('{"lookups": 5, "hi')     # crashed mid-write
        self.assertAlmostEqual(persisted_reuse_rate(), 0.9)

    def test_garbage_lines_are_skipped(self):
        _reuse_log_path().parent.mkdir(parents=True, exist_ok=True)
        _reuse_log_path().write_text(
            "not json\n"
            + json.dumps({"lookups": 10, "hits": 5, "ts": _now()}) + "\n"
            + "\n"
            + '["wrong", "shape"]\n'
        )
        self.assertAlmostEqual(persisted_reuse_rate(), 0.5)

    def test_unreadable_log_returns_none_not_an_exception(self):
        path = _reuse_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()          # a directory where a file is expected
        self.assertIsNone(persisted_reuse_rate())

    def test_flush_failure_keeps_the_delta_pending(self):
        path = _reuse_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()          # make the append fail
        tracker = self._warm_tracker()
        self.assertFalse(tracker.flush_reuse())
        # In-process measurement must remain intact and retryable.
        self.assertIsNotNone(tracker.reuse_rate())


class TestStaleness(_IsolatedState):
    """Advice driven by month-old traffic is worse than advice that admits it
    has no data — the workload it measured may no longer exist."""

    def test_old_records_are_ignored(self):
        from aictl.core.constants import PREFIX_REUSE_MAX_AGE_SECONDS
        import time as _time

        old = int(_time.time()) - PREFIX_REUSE_MAX_AGE_SECONDS - 60
        _reuse_log_path().parent.mkdir(parents=True, exist_ok=True)
        _reuse_log_path().write_text(
            json.dumps({"lookups": 100, "hits": 100, "ts": old}) + "\n")
        self.assertIsNone(persisted_reuse_rate())

    def test_untimestamped_records_are_treated_as_stale(self):
        # Written before `ts` existed; age is unknowable, so don't trust it.
        _reuse_log_path().parent.mkdir(parents=True, exist_ok=True)
        _reuse_log_path().write_text('{"lookups":100,"hits":100}\n')
        self.assertIsNone(persisted_reuse_rate())

    def test_fresh_records_are_kept(self):
        tracker = self._warm_tracker(hits=9, misses=1)
        tracker.flush_reuse()
        self.assertAlmostEqual(persisted_reuse_rate(), 0.9)

    def test_mixed_ages_use_only_the_fresh_ones(self):
        from aictl.core.constants import PREFIX_REUSE_MAX_AGE_SECONDS
        import time as _time

        old = int(_time.time()) - PREFIX_REUSE_MAX_AGE_SECONDS - 60
        path = _reuse_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"lookups": 100, "hits": 0, "ts": old}) + "\n"
            + json.dumps({"lookups": 10, "hits": 10, "ts": int(_time.time())}) + "\n")
        # Only the fresh record counts: 10/10, not 10/110.
        self.assertAlmostEqual(persisted_reuse_rate(), 1.0)

    def test_truncation_output_is_readable_afterwards(self):
        # The collapsed summary must carry a timestamp, or trimming would
        # silently render the whole history stale.
        path = _reuse_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        now = int(__import__("time").time())
        with open(path, "w", encoding="utf-8") as fh:
            for _ in range(2500):
                fh.write(json.dumps({"lookups": 2, "hits": 1, "ts": now}) + "\n")
        truncate_reuse_log()
        self.assertAlmostEqual(persisted_reuse_rate(), 0.5)


class TestTruncation(_IsolatedState):
    def test_below_bound_is_left_alone(self):
        tracker = self._warm_tracker()
        tracker.flush_reuse()
        before = _reuse_log_path().read_text()
        truncate_reuse_log()
        self.assertEqual(_reuse_log_path().read_text(), before)

    def test_truncation_preserves_the_rate(self):
        path = _reuse_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for _ in range(2500):
                fh.write(json.dumps({"lookups": 2, "hits": 1, "ts": _now()}) + "\n")
        before = persisted_reuse_rate()
        truncate_reuse_log()
        self.assertAlmostEqual(persisted_reuse_rate(), before)
        self.assertEqual(len(path.read_text().strip().splitlines()), 1)

    def test_truncation_keeps_totals_not_just_recent_history(self):
        path = _reuse_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for _ in range(2500):
                fh.write(json.dumps({"lookups": 4, "hits": 3, "ts": _now()}) + "\n")
        truncate_reuse_log()
        rec = json.loads(path.read_text().strip())
        self.assertEqual(rec["lookups"], 10000)
        self.assertEqual(rec["hits"], 7500)

    def test_missing_log_truncates_without_error(self):
        truncate_reuse_log()   # must not raise


class TestAutoFlush(_IsolatedState):
    """Auto-flush is opt-in: writing from the routing path is only justified
    in the long-lived daemon. Left on by default, every CLI run and test would
    drop files into the user's state dir for a measurement nothing reads."""

    def test_disabled_by_default_writes_nothing(self):
        from aictl.core.constants import PREFIX_REUSE_FLUSH_EVERY

        tracker = PrefixRouteTracker()
        for i in range(PREFIX_REUSE_FLUSH_EVERY * 3):
            tracker.best_endpoint(f"p {i} " * 30, ENDPOINTS)
        self.assertFalse(_reuse_log_path().exists(),
                         "wrote to the state dir without being opted in")
        # The in-process measurement is still exact.
        self.assertEqual(tracker.reuse_rate(), 0.0)

    def test_flush_fires_on_the_interval_once_enabled(self):
        from aictl.core.constants import PREFIX_REUSE_FLUSH_EVERY
        tracker = PrefixRouteTracker()
        tracker.enable_persistence()
        for i in range(PREFIX_REUSE_FLUSH_EVERY - 1):
            tracker.best_endpoint(f"p {i} " * 30, ENDPOINTS)
        self.assertFalse(_reuse_log_path().exists(),
                         "flushed before reaching the interval")
        tracker.best_endpoint("one more " * 30, ENDPOINTS)
        self.assertTrue(_reuse_log_path().exists(),
                        "did not flush on reaching the interval")

    def test_explicit_flush_works_even_when_auto_flush_is_off(self):
        tracker = self._warm_tracker()
        self.assertTrue(tracker.flush_reuse())
        self.assertIsNotNone(persisted_reuse_rate())

    def test_routing_still_works_when_the_log_is_unwritable(self):
        from aictl.core.constants import PREFIX_REUSE_FLUSH_EVERY
        path = _reuse_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()

        tracker = PrefixRouteTracker()
        tracker.enable_persistence()
        tracker.record(ENDPOINTS[0], SHARED)
        for _ in range(PREFIX_REUSE_FLUSH_EVERY + 5):
            match = tracker.best_endpoint(SHARED, ENDPOINTS)
        self.assertIsNotNone(match)          # routing unaffected by I/O failure
        self.assertEqual(tracker.reuse_rate(), 1.0)

    def test_concurrent_flushes_do_not_lose_or_duplicate_counts(self):
        # Several threads can see a flush as due at once. Each delta must be
        # written exactly once, so the persisted total equals the in-process
        # total no matter how the records got split up.
        import threading

        tracker = PrefixRouteTracker()
        tracker.enable_persistence()
        tracker.record(ENDPOINTS[0], SHARED)

        def hammer():
            for _ in range(200):
                tracker.best_endpoint(SHARED, ENDPOINTS)

        threads = [threading.Thread(target=hammer) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        tracker.flush_reuse()      # drain whatever remains

        persisted = 0
        with open(_reuse_log_path(), encoding="utf-8") as fh:
            for line in fh:
                persisted += json.loads(line)["lookups"]
        self.assertEqual(persisted, tracker.stats()["lookups"])


class TestAdvisorFallbackOrder(_IsolatedState):
    def test_persisted_rate_reaches_a_fresh_advisor(self):
        from aictl.runtime.kv_offload import measured_prefix_reuse
        from aictl.runtime.prefix_route import get_default_tracker

        get_default_tracker().clear()        # simulate a fresh process
        tracker = PrefixRouteTracker()
        for i in range(20):                  # one-shot traffic: 0% reuse
            tracker.best_endpoint(f"unique {i} " * 30, ENDPOINTS)
        tracker.flush_reuse()

        try:
            self.assertEqual(measured_prefix_reuse(), 0.0)
        finally:
            get_default_tracker().clear()

    def test_in_process_measurement_wins_over_the_log(self):
        from aictl.runtime.kv_offload import measured_prefix_reuse
        from aictl.runtime.prefix_route import get_default_tracker

        # Log says 0% reuse...
        stale = PrefixRouteTracker()
        for i in range(20):
            stale.best_endpoint(f"unique {i} " * 30, ENDPOINTS)
        stale.flush_reuse()

        # ...but this process is observing 100%, which is exact and current.
        live = get_default_tracker()
        live.clear()
        live.record(ENDPOINTS[0], SHARED)
        for _ in range(5):
            live.best_endpoint(SHARED, ENDPOINTS)
        try:
            self.assertEqual(measured_prefix_reuse(), 1.0)
        finally:
            live.clear()

    def test_no_measurement_anywhere_is_none(self):
        from aictl.runtime.kv_offload import measured_prefix_reuse
        from aictl.runtime.prefix_route import get_default_tracker

        get_default_tracker().clear()
        self.assertIsNone(measured_prefix_reuse())


if __name__ == "__main__":
    unittest.main()
