"""Pass 136: `context gc --max-age` negative must not wipe live snapshots.

`ContextContinuityEngine.gc` computed `cutoff = now - max_age_hours*3600` with no
guard. A NEGATIVE max-age makes the cutoff a FUTURE timestamp, so
`snap.created_at < cutoff` is True for EVERY snapshot — even ones saved this
second — silently garbage-collecting all live context. Same destructive
future-cutoff class as the `audit purge --max-age` bug (Pass 134).

Verified: `gc(max_age_hours=-1)` deleted a fresh `saved` snapshot.

Fix:
  - CLI `context gc --max-age` uses type=nonneg_int — a negative is rejected at
    parse time (exit 2) with a clean usage error.
  - The engine floors `max_age_hours = max(0, ...)` as defense-in-depth for SDK
    callers building the call directly; the cutoff can no longer reach into the
    future, so future-dated/just-saved snapshots survive a stray negative.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path


def _engine():
    from aictl.runtime.continuity import ContextContinuityEngine
    return ContextContinuityEngine(Path(tempfile.mkdtemp()))


def _snap(sid, created_at, status="saved"):
    from aictl.runtime.continuity import ContextSnapshot
    return ContextSnapshot(snapshot_id=sid, engine="vllm", model="m",
                           created_at=created_at, status=status)


class TestGcNegativeMaxAge(unittest.TestCase):
    def test_future_cutoff_no_longer_reaches_future(self):
        # The core regression: a future-dated snapshot must survive gc(-1).
        eng = _engine()
        eng._save_index([_snap("future1", time.time() + 3600)])
        removed = eng.gc(max_age_hours=-1)
        self.assertEqual(removed, 0)
        self.assertEqual(len(eng._load_index()), 1)

    def test_negative_floored_to_zero_not_future(self):
        # gc(-100) behaves like gc(0) (cutoff == now), never like a future cutoff.
        eng = _engine()
        eng._save_index([_snap("future1", time.time() + 7200)])
        self.assertEqual(eng.gc(max_age_hours=-100), 0)

    def test_positive_max_age_still_collects_old(self):
        eng = _engine()
        old = time.time() - (48 * 3600)
        eng._save_index([_snap("old1", old), _snap("new1", time.time())])
        removed = eng.gc(max_age_hours=24)
        self.assertEqual(removed, 1)                 # only the 48h-old one
        self.assertEqual(len(eng._load_index()), 1)

    def test_expired_status_still_collected(self):
        # gc also drops expired/failed regardless of age — unchanged behavior.
        eng = _engine()
        eng._save_index([_snap("exp1", time.time(), status="expired")])
        self.assertEqual(eng.gc(max_age_hours=24), 1)


class TestGcCliRejectsNegative(unittest.TestCase):
    def _build_parser(self):
        import argparse
        from aictl.cmd import context
        p = argparse.ArgumentParser(prog="aictl")
        sub = p.add_subparsers()
        context.register(sub)
        return p

    def test_negative_max_age_exit_2(self):
        parser = self._build_parser()
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(["context", "gc", "--max-age", "-1"])
        self.assertEqual(cm.exception.code, 2)

    def test_zero_and_positive_accepted(self):
        parser = self._build_parser()
        self.assertEqual(parser.parse_args(["context", "gc", "--max-age", "0"]).max_age, 0)
        self.assertEqual(parser.parse_args(["context", "gc", "--max-age", "48"]).max_age, 48)


if __name__ == "__main__":
    unittest.main()
