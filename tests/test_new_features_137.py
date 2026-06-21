"""Pass 137: sweep the future-cutoff data-loss class (snapshot/model/cache).

Passes 134 & 136 fixed the `cutoff = now - N*unit` future-cutoff trap in
`audit purge` and `context gc`. The same pattern lived in three more purge/clean
paths, all destructive when N is negative (cutoff becomes a FUTURE timestamp,
so `created/accessed < cutoff` matches EVERY item — including ones written this
second):

  - `snapshot purge --max-age`  -> deletes all but --keep newest snapshots
  - `model cleanup --days`      -> flags every registered model (incl. fresh) stale
  - `model cache --days` / find_stale -> wipes the whole model cache

Verified via real CLI: `model cleanup --days -1 --dry-run` listed a
just-registered model as a deletion candidate.

Fix (consistent with Passes 134/136): the `--max-age`/`--days` flags use
type=nonneg_int (reject negatives at parse time, exit 2; 0 = "purge all up to
now" stays legitimate), and the handlers/data layer floor the value
(max(0, ...)) so a stray negative from an SDK caller can no longer make the
cutoff reach into the future.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import time
import unittest


def _parser_for(register):
    p = argparse.ArgumentParser(prog="aictl")
    sub = p.add_subparsers()
    register(sub)
    return p


def _expect_exit2(tc, parser, argv):
    with tc.assertRaises(SystemExit) as cm:
        with contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(argv)
    tc.assertEqual(cm.exception.code, 2)


class TestParseTimeRejection(unittest.TestCase):
    def test_snapshot_purge_max_age_negative(self):
        from aictl.cmd import snapshot
        _expect_exit2(self, _parser_for(snapshot.register),
                      ["snapshot", "purge", "--max-age", "-1"])

    def test_snapshot_purge_keep_negative(self):
        from aictl.cmd import snapshot
        _expect_exit2(self, _parser_for(snapshot.register),
                      ["snapshot", "purge", "--keep", "-2"])

    def test_model_cleanup_days_negative(self):
        from aictl.cmd import model
        _expect_exit2(self, _parser_for(model.register),
                      ["model", "cleanup", "--days", "-1"])

    def test_model_cache_days_negative(self):
        from aictl.cmd import model
        _expect_exit2(self, _parser_for(model.register),
                      ["model", "cache", "--days", "-5"])

    def test_zero_accepted_everywhere(self):
        from aictl.cmd import snapshot, model
        sp = _parser_for(snapshot.register)
        self.assertEqual(sp.parse_args(["snapshot", "purge", "--max-age", "0"]).max_age, 0)
        mp = _parser_for(model.register)
        self.assertEqual(mp.parse_args(["model", "cleanup", "--days", "0"]).days, 0)


class TestCacheFindStaleFloor(unittest.TestCase):
    """find_stale must floor days at 0 — a negative can't reach into the future."""

    def _report(self, last_accessed):
        from aictl.runtime.cache import CacheReport, CacheEntry
        r = CacheReport()
        r.entries = [CacheEntry(path="/x", name="x", size_bytes=1,
                                last_accessed=last_accessed, source="custom")]
        return r

    def test_future_dated_entry_not_wiped_by_negative(self):
        from aictl.runtime.cache import find_stale
        # Old future-cutoff: days=-1 -> cutoff=now+1d -> future entry (now+1h)
        # matched and wiped. Floored: cutoff=now -> not matched.
        r = self._report(time.time() + 3600)
        self.assertEqual(find_stale(r, days=-1), [])

    def test_positive_days_still_finds_old(self):
        from aictl.runtime.cache import find_stale
        r = self._report(time.time() - 60 * 86400)   # 60 days old
        self.assertEqual(len(find_stale(r, days=30)), 1)


class TestModelCleanupFloor(unittest.TestCase):
    def test_negative_days_does_not_flag_future_model(self):
        # SDK/Namespace path bypasses the parser; the handler floors days.
        from aictl.cmd import model
        import tempfile
        from pathlib import Path
        from aictl.core.state import StateStore
        d = Path(tempfile.mkdtemp())
        store = StateStore(d)
        store.register_model("m1", "future-model", "sha256:abc",
                             registered_at=time.time() + 3600, status="available")
        ns = argparse.Namespace(state_dir=str(d), days=-1, status="",
                                dry_run=True, json=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            model.run_cleanup(ns)
        # Future-dated model must NOT be a deletion candidate after flooring: its
        # name never appears in the (empty-candidate) cleanup output.
        self.assertNotIn("future-model", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
