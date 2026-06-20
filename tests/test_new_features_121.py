"""Pass 121 (loop): `warmup --top` negative-slice trap (Pass 112 class, head form).

`get_warmup_candidates` returned `scored[:top_n]`. With a negative top_n this is
the classic slice trap — `scored[:-3]` returns *all but the last 3* — so
`aictl warmup run --top -3` warmed up (library_size - 3) models: the inverse of
limiting to a few, and an unbounded set that grows with the model library (a
user with 50 models would warm 47, a mass GPU load).

Fixes, defense-in-depth:
  - library `get_warmup_candidates` now returns [] for top_n <= 0, so no caller
    can ever trigger the inverted slice (cf. Pass 112's EventBus.recent /
    perf.read_recent guards, tail form);
  - CLI `warmup run` and `warmup schedule` reject `--top < 1` with a clear
    message and a non-zero exit (and schedule does not persist a bad value).
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import unittest
from pathlib import Path

from aictl.runtime.warmup import WarmupManager
from aictl.core.state import StateStore


def _seed(n=5):
    d = tempfile.mkdtemp()
    mgr = WarmupManager(StateStore(d))
    p = mgr._usage_path() if callable(mgr._usage_path) else mgr._usage_path
    p.write_text(json.dumps({
        f"m{i}": {"model": f"model{i}", "engine": "ollama", "count": i + 1,
                  "last_used": time.time(), "avg_load_time_ms": 100}
        for i in range(n)
    }))
    return d, mgr


class TestWarmupCandidateGuard(unittest.TestCase):
    def test_negative_top_n_returns_empty_not_inverted_slice(self):
        _d, mgr = _seed(5)
        # Was: scored[:-3] == 2 arbitrary models. Now: [].
        self.assertEqual(mgr.get_warmup_candidates(top_n=-3), [])

    def test_zero_top_n_returns_empty(self):
        _d, mgr = _seed(5)
        self.assertEqual(mgr.get_warmup_candidates(top_n=0), [])

    def test_positive_top_n_unchanged(self):
        _d, mgr = _seed(5)
        got = mgr.get_warmup_candidates(top_n=3)
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0].model, "model4")  # highest count → top


class TestWarmupCmdValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _run_args(self, top):
        return argparse.Namespace(top=top, state_dir=self.tmp, json=False)

    def _sched_args(self, top):
        return argparse.Namespace(every="1h", top=top, state_dir=self.tmp,
                                  json=False)

    def test_run_negative_top_rejected(self):
        from aictl.cmd.warmup import run_warmup
        self.assertEqual(run_warmup(self._run_args(-3)), 1)

    def test_run_zero_top_rejected(self):
        from aictl.cmd.warmup import run_warmup
        self.assertEqual(run_warmup(self._run_args(0)), 1)

    def test_schedule_negative_top_rejected_and_not_persisted(self):
        from aictl.cmd.warmup import run_schedule
        self.assertEqual(run_schedule(self._sched_args(-3)), 1)
        self.assertFalse((Path(self.tmp) / "warmup_schedule.json").exists())

    def test_schedule_valid_top_persists(self):
        from aictl.cmd.warmup import run_schedule
        self.assertEqual(run_schedule(self._sched_args(3)), 0)
        self.assertTrue((Path(self.tmp) / "warmup_schedule.json").exists())


if __name__ == "__main__":
    unittest.main()
