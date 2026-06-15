"""Pass 98 (loop): StateStore stack registry is idempotent AND concurrency-safe.

Idempotency check (running the same apply twice) led to a concurrency gap:
upsert_stack/remove_stack did a load→modify→save on the shared stacks.json
WITHOUT a lock — the same lost-update race fixed for quota/tenant in pass 95, but
in the *core* registry every apply/down touches. 10 concurrent upserts for
distinct stacks left only ~5. Now serialized with file_lock.
"""

from __future__ import annotations

import multiprocessing as mp
import tempfile
import time
import unittest
from pathlib import Path


def _upsert_worker(arg):
    sd, name = arg
    from aictl.core.state import StateStore, StackEntry
    StateStore(Path(sd)).upsert_stack(
        StackEntry(name=name, file="", applied_at=time.time(), status="running", services=[]))


class TestStackIdempotency(unittest.TestCase):

    def test_upsert_same_name_twice_is_one_entry(self):
        from aictl.core.state import StateStore, StackEntry
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            for status in ("starting", "running"):
                store.upsert_stack(StackEntry(name="web", file="", applied_at=time.time(),
                                              status=status, services=[]))
            stacks = store.load_stacks()
            self.assertEqual(len(stacks), 1)
            self.assertEqual(stacks[0].status, "running")  # second upsert wins

    def test_remove_is_idempotent(self):
        from aictl.core.state import StateStore, StackEntry
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            store.upsert_stack(StackEntry(name="web", file="", applied_at=time.time(),
                                          status="running", services=[]))
            self.assertTrue(store.remove_stack("web"))
            self.assertFalse(store.remove_stack("web"))  # second remove: no-op


class TestStackConcurrency(unittest.TestCase):

    def test_concurrent_upserts_no_lost_updates(self):
        with tempfile.TemporaryDirectory() as d:
            names = [f"stack{i}" for i in range(10)]
            ctx = mp.get_context("spawn")
            with ctx.Pool(5) as pool:
                pool.map(_upsert_worker, [(d, n) for n in names])
            from aictl.core.state import StateStore
            survived = {e.name for e in StateStore(Path(d)).load_stacks()}
        self.assertEqual(survived, set(names))


if __name__ == "__main__":
    unittest.main()
