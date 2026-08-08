"""Pass 196: drain prefix-reuse counters on daemon shutdown.

Item X shipped with a noted gap: auto-flush fires only every
PREFIX_REUSE_FLUSH_EVERY lookups, so up to that many lookups' worth of
measurement was lost whenever the daemon exited. Immaterial to a rate over a
long run, but it meant a daemon restarted more often than it flushed could
persist nothing at all — the exact case where the measurement matters least
being indistinguishable from the case where it matters most.

`drain_reuse_counters()` is deliberately a named module function rather than
logic inline in the signal handler: shutdown paths are the least-exercised
code in a daemon, and one buried in a closure could not be tested at all.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from aictl.daemon.aiosd import drain_reuse_counters
from aictl.runtime.prefix_route import (
    _reuse_log_path,
    get_default_tracker,
    persisted_reuse_rate,
)

ENDPOINTS = ["http://a:8000"]
SHARED = "SHARED PREAMBLE for the drain test, long enough to hash. " * 25


class TestShutdownDrain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("AICTL_STATE_DIR")
        os.environ["AICTL_STATE_DIR"] = self._tmp.name
        # The singleton is shared, and a daemon test elsewhere may have opted
        # it into persistence. Pin the state these tests assume rather than
        # inheriting whatever ran first.
        self._prev_persist = get_default_tracker().persistence_enabled()
        get_default_tracker().enable_persistence(False)
        get_default_tracker().clear()

    def tearDown(self):
        get_default_tracker().enable_persistence(self._prev_persist)
        get_default_tracker().clear()
        if self._prev is None:
            os.environ.pop("AICTL_STATE_DIR", None)
        else:
            os.environ["AICTL_STATE_DIR"] = self._prev
        self._tmp.cleanup()

    def test_sub_interval_counts_survive_shutdown(self):
        from aictl.core.constants import PREFIX_REUSE_FLUSH_EVERY

        tracker = get_default_tracker()
        tracker.record(ENDPOINTS[0], SHARED)
        # Deliberately fewer than one flush interval: without the drain this
        # measurement would vanish entirely on exit.
        for _ in range(PREFIX_REUSE_FLUSH_EVERY // 2):
            tracker.best_endpoint(SHARED, ENDPOINTS)

        self.assertIsNone(persisted_reuse_rate(), "flushed early; test is not meaningful")
        self.assertTrue(drain_reuse_counters())
        self.assertEqual(persisted_reuse_rate(), 1.0)

    def test_drain_persists_the_exact_counts(self):
        tracker = get_default_tracker()
        for i in range(3):
            tracker.best_endpoint(f"cold {i} " * 30, ENDPOINTS)
        tracker.record(ENDPOINTS[0], SHARED)
        for _ in range(7):
            tracker.best_endpoint(SHARED, ENDPOINTS)

        drain_reuse_counters()
        with open(_reuse_log_path(), encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(sum(r["lookups"] for r in records), 10)
        self.assertEqual(sum(r["hits"] for r in records), 7)

    def test_drain_with_nothing_pending_is_a_noop(self):
        self.assertFalse(drain_reuse_counters())
        self.assertIsNone(persisted_reuse_rate())

    def test_double_drain_does_not_double_count(self):
        tracker = get_default_tracker()
        tracker.record(ENDPOINTS[0], SHARED)
        for _ in range(5):
            tracker.best_endpoint(SHARED, ENDPOINTS)

        self.assertTrue(drain_reuse_counters())
        self.assertFalse(drain_reuse_counters())
        with open(_reuse_log_path(), encoding="utf-8") as fh:
            total = sum(json.loads(line)["lookups"] for line in fh if line.strip())
        self.assertEqual(total, 5)

    def test_drain_never_raises_when_the_log_is_unwritable(self):
        # This runs inside a signal handler; an exception would derail shutdown.
        path = _reuse_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()
        tracker = get_default_tracker()
        tracker.record(ENDPOINTS[0], SHARED)
        tracker.best_endpoint(SHARED, ENDPOINTS)
        self.assertFalse(drain_reuse_counters())   # returns, does not raise

    def test_drain_works_without_auto_flush_enabled(self):
        # A daemon that never reached its flush interval still has counts
        # worth writing; the drain must not depend on the opt-in.
        tracker = get_default_tracker()
        tracker.enable_persistence(False)
        tracker.record(ENDPOINTS[0], SHARED)
        tracker.best_endpoint(SHARED, ENDPOINTS)
        self.assertTrue(drain_reuse_counters())


class TestShutdownWiring(unittest.TestCase):
    def test_shutdown_handler_calls_the_drain(self):
        # Guards against the drain being dropped from the signal handler in a
        # later refactor — the failure would be silent data loss on exit.
        import inspect

        from aictl.daemon import aiosd

        source = inspect.getsource(aiosd.serve)
        self.assertIn("drain_reuse_counters()", source)


if __name__ == "__main__":
    unittest.main()
