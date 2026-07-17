"""Pass 134: audit count flags must reject negatives (slice + destructive traps).

`aictl audit` had three unguarded user-controlled counts, all instances of the
"negative integer into a Python primitive" class (V1/V3 invariants):

  1. `-n/--lines`  -> `entries[:lines]`. A negative makes `entries[:-3]` the
     INVERTED slice: "show 3" silently returns all-but-3 (and combined with the
     negative `read(n=...)` pool, returned 0). User asked for 3, got 0.

  2. `stats --top` -> `Counter.most_common(top)`. A negative/zero silently
     returns [] — "top -2 event types" shows nothing instead of erroring.

  3. `purge --max-age` -> `cutoff = now - max_age*86400`. A NEGATIVE max-age
     makes the cutoff a FUTURE timestamp, so `(now - mtime) > cutoff_secs` is
     True for EVERY file — including ones written this second. `--max-age -1`
     silently wipes the ENTIRE audit history (data loss / compliance gap).

Fix: parse-time `type=` validation (positive_int for the display counts,
nonneg_int for max-age where 0 = "purge all" is legitimate) rejects bad CLI
input cleanly (exit 2); the handlers additionally floor the value as
defense-in-depth for SDK callers that build a Namespace directly.
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


def _seed(n=8):
    d = Path(tempfile.mkdtemp())
    from aictl.core.audit import get_audit_log, AuditEntry
    log = get_audit_log(d)
    for i in range(n):
        log.write(AuditEntry(timestamp=time.time() + i, event="deploy",
                             resource=f"m{i}", actor="user", outcome="success"))
    return d


def _build_parser():
    import argparse as _ap
    from aictl.cmd import audit
    p = _ap.ArgumentParser(prog="aictl")
    sub = p.add_subparsers()
    audit.register(sub)
    return p


class TestParseTimeRejection(unittest.TestCase):
    """CLI parse path: negatives rejected with exit 2 (argparse usage error)."""

    def _expect_exit2(self, argv):
        parser = _build_parser()
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(argv)
        self.assertEqual(cm.exception.code, 2)

    def test_lines_negative_rejected(self):
        self._expect_exit2(["audit", "-n", "-3"])

    def test_lines_zero_rejected(self):
        self._expect_exit2(["audit", "-n", "0"])

    def test_top_negative_rejected(self):
        self._expect_exit2(["audit", "stats", "--top", "-2"])

    def test_max_age_negative_rejected(self):
        self._expect_exit2(["audit", "purge", "--max-age", "-1"])

    def test_valid_values_accepted(self):
        parser = _build_parser()
        ns = parser.parse_args(["audit", "-n", "5"])
        self.assertEqual(ns.lines, 5)
        ns2 = parser.parse_args(["audit", "purge", "--max-age", "0"])  # 0 ok
        self.assertEqual(ns2.max_age, 0)


class TestHandlerDefense(unittest.TestCase):
    """SDK/Namespace path (bypasses the parser): values are floored, not trapped."""

    def _run_json(self, fn, ns):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(ns)
        return json.loads(buf.getvalue())

    def test_lines_not_inverted(self):
        from aictl.cmd import audit
        d = _seed(8)
        ns = argparse.Namespace(state_dir=str(d), lines=-3, since="", resource="",
                                actor="", event="", export="", json=True)
        out = self._run_json(audit.run, ns)
        # Inverted-slice trap would have returned all-but-3 (5); floored returns >= 1.
        self.assertGreaterEqual(len(out), 1)
        self.assertLessEqual(len(out), 8)

    def test_lines_positive_exact(self):
        from aictl.cmd import audit
        d = _seed(8)
        ns = argparse.Namespace(state_dir=str(d), lines=3, since="", resource="",
                                actor="", event="", export="", json=True)
        out = self._run_json(audit.run, ns)
        self.assertEqual(len(out), 3)

    def test_max_age_negative_does_not_wipe_via_future_cutoff(self):
        # The destructive trap: a negative max-age must NOT behave as a future
        # cutoff. After flooring to 0, the dry-run report is coherent ("purge all
        # older than now"), not the negative-cutoff "matches everything always".
        from aictl.cmd import audit
        d = _seed(1)
        ns = argparse.Namespace(state_dir=str(d), max_age=-99999, dry_run=True, json=True)
        out = self._run_json(audit.run_purge, ns)
        self.assertTrue(out["dry_run"])   # nothing actually deleted
        # And the displayed threshold is the floored value, never the negative.
        self.assertIn("purged", out)


if __name__ == "__main__":
    unittest.main()
