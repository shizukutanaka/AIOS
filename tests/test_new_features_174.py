"""Pass 174 (IMPROVEMENTS.md item J, last open sub-item): emit
aios_guard_redactions_total.

docs/IMPROVEMENTS.md's own status note on item J ("Observability of the
value props") says: "Route-savings/guard-redaction counters still need
persistent tallies and remain future work." Cache/metering/cascade counters
were already wired to /metrics; guard redactions were not -- aictl guard
scan --redact could redact PII all day and nothing durable ever recorded
how much.

Fix: core/guard.py's scan() gains an optional state_dir kwarg. Passing one
persists a lifetime "total_redactions" tally (guard_stats.json, atomic
write, best-effort -- a corrupt/missing stats file must never break a real
scan/redact call). Left at its default (None), scan() stays the exact pure
function it always was -- no disk I/O, so the many existing tests calling
scan(text, redact_pii=True) without a state_dir are unaffected. Only
`aictl guard scan --redact` (cmd/guard.py, resolves a concrete StateStore
so the counter is fed regardless of whether --state-dir was passed) opts
in. metrics/prometheus.py reads the tally into a new
aios_guard_redactions_total counter, following the exact pattern already
used for aios_route_cascade_*_total (skip the metric entirely if the stats
file doesn't exist yet, rather than emitting a fake zero).

Route-cost-saved (the other IMPROVEMENTS.md item J leftover) is NOT done in
this pass -- it needs a real methodology decision (cost saved vs which
baseline?) rather than a mechanical counter-add, and is left as noted
future work.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path


class TestScanStaysPureWithoutStateDir(unittest.TestCase):
    """The core invariant: scan() must not touch disk unless a state_dir is
    explicitly given -- most callers (all existing tests, library use) must
    see zero behavior change."""

    def test_no_state_dir_no_file_created(self):
        from aictl.core.guard import scan, _guard_stats_path
        with tempfile.TemporaryDirectory() as d:
            # Prove no file appears anywhere reachable by pointing DEFAULT
            # at this tmp dir and confirming scan() still doesn't write it.
            import aictl.core.state as state_mod
            orig = state_mod.DEFAULT_STATE_DIR
            state_mod.DEFAULT_STATE_DIR = Path(d)
            try:
                scan("email me at a@b.com", redact_pii=True)
                self.assertFalse((Path(d) / "guard_stats.json").exists())
            finally:
                state_mod.DEFAULT_STATE_DIR = orig

    def test_existing_call_signature_unaffected(self):
        from aictl.core.guard import scan
        result, processed = scan("email me at a@b.com", redact_pii=True)
        self.assertTrue(result.pii)
        self.assertIn("[REDACTED]", processed)


class TestRedactionStatPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _stats(self):
        return json.loads((self.d / "guard_stats.json").read_text())

    def test_redaction_with_state_dir_persists_count(self):
        from aictl.core.guard import scan
        # Two distinct, non-overlapping email matches -- avoids coupling
        # this test to detect_pii's own overlapping-pattern quirks (e.g. a
        # phone number can also match the postal-code pattern).
        scan("email me at a@b.com or fallback@c.com",
            redact_pii=True, state_dir=self.d)
        stats = self._stats()
        self.assertEqual(stats["total_redactions"], 2)

    def test_counts_accumulate_across_calls(self):
        from aictl.core.guard import scan
        scan("a@b.com", redact_pii=True, state_dir=self.d)
        scan("c@d.com", redact_pii=True, state_dir=self.d)
        self.assertEqual(self._stats()["total_redactions"], 2)

    def test_no_pii_no_redaction_no_file(self):
        from aictl.core.guard import scan
        scan("perfectly clean text", redact_pii=True, state_dir=self.d)
        self.assertFalse((self.d / "guard_stats.json").exists())

    def test_redact_pii_false_does_not_persist_even_with_state_dir(self):
        from aictl.core.guard import scan
        scan("a@b.com", redact_pii=False, state_dir=self.d)
        self.assertFalse((self.d / "guard_stats.json").exists())

    def test_corrupt_stats_file_degrades_gracefully(self):
        from aictl.core.guard import scan
        path = self.d / "guard_stats.json"
        path.write_text("not json{{{")
        # Must not raise -- degrades to a fresh counter starting at 0(+found).
        scan("a@b.com", redact_pii=True, state_dir=self.d)
        self.assertEqual(self._stats()["total_redactions"], 1)

    def test_non_dict_stats_file_degrades_gracefully(self):
        from aictl.core.guard import scan
        path = self.d / "guard_stats.json"
        path.write_text("[1, 2, 3]")
        scan("a@b.com", redact_pii=True, state_dir=self.d)
        self.assertEqual(self._stats()["total_redactions"], 1)

    def test_scan_never_raises_if_stats_write_fails(self):
        # Point state_dir at a path that can't be created (a file, not a dir)
        # -- the redaction itself must still succeed; only the stat write
        # is best-effort.
        from aictl.core.guard import scan
        blocker = self.d / "not_a_dir"
        blocker.write_text("i am a file")
        result, processed = scan("a@b.com", redact_pii=True, state_dir=blocker / "sub")
        self.assertIn("[REDACTED]", processed)


class TestGuardScanCLIPersistsRedactions(unittest.TestCase):
    def test_run_scan_with_redact_and_state_dir_persists(self):
        from aictl.cmd.guard import run_scan
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(text="contact a@b.com", file=None,
                                      redact=True, block_pii=False,
                                      state_dir=tmp, json=False)
            run_scan(args)
            stats = json.loads((Path(tmp) / "guard_stats.json").read_text())
            self.assertEqual(stats["total_redactions"], 1)

    def test_run_scan_without_redact_does_not_persist(self):
        from aictl.cmd.guard import run_scan
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(text="contact a@b.com", file=None,
                                      redact=False, block_pii=False,
                                      state_dir=tmp, json=False)
            run_scan(args)
            self.assertFalse((Path(tmp) / "guard_stats.json").exists())

    def test_run_scan_persists_even_without_explicit_state_dir_flag(self):
        # The whole point: real users rarely pass --state-dir, so the CLI
        # must resolve a concrete default dir itself, not skip persistence
        # just because args.state_dir happens to be None.
        from aictl.cmd.guard import run_scan
        import aictl.core.state as state_mod
        with tempfile.TemporaryDirectory() as tmp:
            orig = state_mod.DEFAULT_STATE_DIR
            state_mod.DEFAULT_STATE_DIR = Path(tmp)
            try:
                args = argparse.Namespace(text="contact a@b.com", file=None,
                                          redact=True, block_pii=False,
                                          state_dir=None, json=False)
                run_scan(args)
                stats = json.loads((Path(tmp) / "guard_stats.json").read_text())
                self.assertEqual(stats["total_redactions"], 1)
            finally:
                state_mod.DEFAULT_STATE_DIR = orig


class TestPrometheusEmitsGuardRedactions(unittest.TestCase):
    def test_metric_present_when_stats_file_exists(self):
        from aictl.metrics.prometheus import _emit_value_prop_metrics
        import aictl.core.guard as guard_mod
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "guard_stats.json").write_text(
                json.dumps({"total_redactions": 42}))
            orig = guard_mod._guard_stats_path
            guard_mod._guard_stats_path = lambda state_dir=None: Path(tmp) / "guard_stats.json"
            try:
                lines: list[str] = []
                _emit_value_prop_metrics(lines)
                text = "\n".join(lines)
                self.assertIn("aios_guard_redactions_total", text)
                self.assertIn("aios_guard_redactions_total 42", text)
            finally:
                guard_mod._guard_stats_path = orig

    def test_metric_absent_when_stats_file_missing(self):
        from aictl.metrics.prometheus import _emit_value_prop_metrics
        import aictl.core.guard as guard_mod
        with tempfile.TemporaryDirectory() as tmp:
            orig = guard_mod._guard_stats_path
            guard_mod._guard_stats_path = lambda state_dir=None: Path(tmp) / "nonexistent.json"
            try:
                lines: list[str] = []
                _emit_value_prop_metrics(lines)
                text = "\n".join(lines)
                self.assertNotIn("aios_guard_redactions_total", text)
            finally:
                guard_mod._guard_stats_path = orig

    def test_full_metrics_text_generation_does_not_crash(self):
        from aictl.metrics.prometheus import generate_metrics_text
        from aictl.core.state import StateStore
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(tmp)
            text = generate_metrics_text(store)
            self.assertIsInstance(text, str)
            self.assertIn("aios_node_info", text)


if __name__ == "__main__":
    unittest.main()
