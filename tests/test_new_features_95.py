"""Pass 95 (loop, Socratic new perspective): concurrency-safe state updates.

New lens: concurrent writers, not just a single process. Atomic writes keep one
write from corrupting a file, but a read-modify-write across processes still
loses updates: 8 concurrent `quota create` for distinct teams left only 1 team —
each process read the registry, added its team, and wrote the whole dict back
(last writer wins). file_lock now serializes the load→modify→save so every
update survives.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import tempfile
import unittest


def _quota_worker(arg):
    state_dir, team = arg
    os.environ["AIOS_STATE_DIR"] = state_dir
    from aictl.cmd.quota import run_create
    import io
    from contextlib import redirect_stdout
    with redirect_stdout(io.StringIO()):
        return run_create(argparse.Namespace(team=team, tokens_per_month=1000,
                                              priority="normal", json=False))


class TestFileLock(unittest.TestCase):

    def test_lock_yields_and_creates_lockfile(self):
        from aictl.core.filelock import file_lock
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "reg.json")
            with file_lock(target):
                pass
            self.assertTrue(os.path.exists(target + ".lock"))

    def test_lock_creates_parent(self):
        from aictl.core.filelock import file_lock
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "sub", "reg.json")
            with file_lock(target):
                pass  # must not raise even though parent didn't exist
            self.assertTrue(os.path.isdir(os.path.join(d, "sub")))


class TestConcurrentQuotaCreate(unittest.TestCase):

    def test_concurrent_creates_have_no_lost_updates(self):
        teams = [f"team{i}" for i in range(12)]
        with tempfile.TemporaryDirectory() as d:
            ctx = mp.get_context("spawn")
            with ctx.Pool(4) as pool:
                rcs = pool.map(_quota_worker, [(d, t) for t in teams])
            self.assertTrue(all(rc == 0 for rc in rcs))
            os.environ["AIOS_STATE_DIR"] = d
            from aictl.cmd.quota import _load
            survived = set(_load()["teams"].keys())
        # Every concurrently-created team must survive (pre-fix: only ~1 did).
        self.assertEqual(survived, set(teams))


if __name__ == "__main__":
    unittest.main()
