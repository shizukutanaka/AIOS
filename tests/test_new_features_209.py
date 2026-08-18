"""Pass 209: cover the two code paths that had no behaviour coverage.

Found by asking a step-1 question of the test suite rather than the product:
which modules does no test exercise? The first, naive answer was "12 modules",
counted by whether a test file names them. That was wrong — all twelve are
imported by `build_parser()`, which many tests call, so `register()` runs for
every one.

Measuring what actually matters — whether each module's `run_*` function is
ever *called* — narrowed twelve to two:

  * `aictl scheduler tick`, whose entire purpose is to let someone drive
    scheduling from cron or a systemd timer instead of running the daemon. A
    regression here breaks exactly the users who chose not to run a daemon,
    and nothing would have caught it.
  * `aictl trust check`, which verifies model files against recorded
    baselines. That is tamper detection, and it had no behaviour coverage at
    all — the worst place in this codebase to have none. `doctor --deep` calls
    the same `check_all()` path internally.

This is the third time this session a "surface count" turned out to overstate
a gap by an order of magnitude (10 observability commands, 12 untested
modules) or invent one entirely (the chunk-boundary injection). The pattern is
consistent enough to be worth naming: counting is not measuring.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.support import IsolatedStateTestCase


class TestSchedulerTick(IsolatedStateTestCase):
    """The manual tick — the daemon-free scheduling path."""

    def _tick(self, use_json=False):
        from aictl.cmd.scheduler import run_tick

        namespace = argparse.Namespace(state_dir=str(self.state_dir), json=use_json)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_tick(namespace)
        output = buffer.getvalue()
        return code, (json.loads(output) if use_json else output)

    def test_nothing_due_is_success_not_an_error(self):
        # A cron job calling this every minute must not report failure just
        # because there was no work — that would page someone nightly.
        code, output = self._tick()
        self.assertEqual(code, 0)
        self.assertIn("Nothing due", output)

    def test_json_shape_is_stable(self):
        code, payload = self._tick(use_json=True)
        self.assertEqual(code, 0)
        self.assertIn("batch_jobs", payload)
        self.assertIn("warmup", payload)
        self.assertIsInstance(payload["batch_jobs"], list)

    def test_empty_state_dir_does_not_raise(self):
        # First run on a fresh machine: nothing has been scheduled yet.
        self.assertEqual(self._tick()[0], 0)

    def test_missing_json_attr_defaults_to_text(self):
        # Namespaces built by other call sites may lack the attribute.
        from aictl.cmd.scheduler import run_tick

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_tick(argparse.Namespace(state_dir=str(self.state_dir)))
        self.assertEqual(code, 0)

    def test_tick_is_idempotent_when_nothing_is_scheduled(self):
        first = self._tick(use_json=True)[1]
        second = self._tick(use_json=True)[1]
        self.assertEqual(first["batch_jobs"], second["batch_jobs"])


class TestTrustCheck(IsolatedStateTestCase):
    """Tamper detection. Uncovered code here is the worst kind."""

    def _run(self, path="", use_json=False):
        from aictl.cmd.trust import run_check

        namespace = argparse.Namespace(state_dir=str(self.state_dir), path=path,
                                       json=use_json)
        # err() writes to stderr, so a stdout-only capture would miss every
        # failure message and silently assert against an empty string.
        out, errbuf = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(errbuf):
            code = run_check(namespace)
        stdout = out.getvalue()
        if use_json and stdout.strip():
            return code, json.loads(stdout)
        return code, stdout + errbuf.getvalue()

    def _baseline(self, content=b"model weights v1"):
        from aictl.cmd.trust import run_baseline

        target = self.state_dir / "model.bin"
        target.write_bytes(content)
        namespace = argparse.Namespace(state_dir=str(self.state_dir),
                                       path=str(target), source="test", json=False)
        with redirect_stdout(io.StringIO()):
            run_baseline(namespace)
        return target

    def test_no_baselines_recorded_is_an_error_not_a_silent_pass(self):
        # Reporting "all clear" when nothing is being watched would be the
        # most dangerous possible output from a tamper check.
        code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn("No baselines", output)

    def test_unknown_path_is_an_error(self):
        code, output = self._run(path=str(self.state_dir / "nope.bin"))
        self.assertEqual(code, 1)
        self.assertIn("No model files", output)

    def test_unmodified_file_passes(self):
        target = self._baseline()
        code, _ = self._run(path=str(target))
        self.assertEqual(code, 0)

    def test_modified_file_is_detected(self):
        # The property the whole feature exists for.
        target = self._baseline()
        target.write_bytes(b"model weights TAMPERED")
        code, output = self._run(path=str(target))
        self.assertNotEqual(code, 0,
                            "a changed file must not check out clean")

    def test_check_all_finds_the_recorded_baseline(self):
        # The path `doctor --deep` uses: audit everything ever baselined,
        # without the caller naming a target.
        self._baseline()
        code, payload = self._run(use_json=True)
        self.assertEqual(code, 0)
        self.assertIn("status", payload)
        self.assertTrue(payload["results"])

    def test_json_reports_status_and_results(self):
        target = self._baseline()
        code, payload = self._run(path=str(target), use_json=True)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(payload.keys()), ["results", "status"])

    def test_tamper_is_visible_in_json_too(self):
        target = self._baseline()
        target.write_bytes(b"different bytes entirely")
        _, payload = self._run(path=str(target), use_json=True)
        self.assertNotEqual(payload["status"], "ok")


class TestCoverageMeasurementItself(unittest.TestCase):
    """Pins the distinction that made this pass small instead of sprawling."""

    def test_parser_import_is_not_behaviour_coverage(self):
        # All twelve "untested" modules are imported by build_parser(), so an
        # import-based coverage measure reports them as covered. Only calling
        # run_* exercises their logic. Keeping this explicit stops the next
        # person re-deriving the same wrong conclusion from the same grep.
        import sys

        before = set(sys.modules)
        from aictl.__main__ import build_parser

        build_parser()
        pulled = {m for m in set(sys.modules) | before if m.startswith("aictl.cmd")}
        self.assertIn("aictl.cmd.scheduler", pulled)
        self.assertIn("aictl.cmd.trust", pulled)


if __name__ == "__main__":
    unittest.main()
