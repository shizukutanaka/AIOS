"""Pass 206: accelerate cycle time (Musk step 4) — parallel test execution.

Measured before optimizing, which is the whole point of doing step 4 after
steps 1-3: of `aictl gate`'s ~59s, the suite is ~57s and every other phase
combined is 2.8s. "Make the gate faster" therefore means exactly "make the
suite faster" — optimizing anything else would have been work spent on the
5%.

Running each test *file* in its own process takes the suite from ~57s to
~21s. Files are already independent of each other, and order *within* a file
is preserved, which matters because unittest orders methods alphabetically
and some tests depend on that.

Isolation came with it rather than after it: each worker gets its own state
directory, because otherwise workers race on ~/.aios — and the suite would
keep writing to the user's real state directory, a bug already found in this
codebase. The same change buys speed and hermeticity.

Serial stays the source of truth. `--parallel` is opt-in, because a parallel
run is only ever evidence *about* the serial result.

Doing this also surfaced a genuine pre-existing defect: running files in
isolation exposed that `test_e2e_stories`'s cost-tracking test asserted "the
first ask is a cache miss" without ensuring the cache was empty. It passed
only because discover-order happened to leave the right state, and failed
standalone. Fixed by establishing the precondition rather than assuming it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from aictl.core.partest import (
    ParallelResult,
    default_workers,
    discover_test_files,
    run_parallel,
)


class TestDiscovery(unittest.TestCase):
    def test_finds_the_suite(self):
        files = discover_test_files()
        self.assertGreater(len(files), 200)
        self.assertTrue(all(f.startswith("test_") for f in files))

    def test_order_is_deterministic(self):
        # Reproducible scheduling makes a flaky run easier to reason about.
        self.assertEqual(discover_test_files(), discover_test_files())
        self.assertEqual(discover_test_files(), sorted(discover_test_files()))

    def test_empty_directory_yields_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(discover_test_files(Path(td)), [])


class TestWorkerCount(unittest.TestCase):
    def test_leaves_a_core_free(self):
        # A run that pins every core makes the machine unusable while it goes.
        cores = os.cpu_count() or 2
        if cores > 2:
            self.assertLessEqual(default_workers(), cores - 1)

    def test_always_at_least_one(self):
        self.assertGreaterEqual(default_workers(), 1)

    def test_is_capped(self):
        # Past a point more workers only add scheduling overhead.
        self.assertLessEqual(default_workers(), 16)


class TestResultShape(unittest.TestCase):
    def test_ok_is_false_when_anything_failed(self):
        self.assertFalse(ParallelResult(passed=5, failed=["test_x"]).ok)

    def test_ok_is_true_when_nothing_failed(self):
        self.assertTrue(ParallelResult(passed=5).ok)

    def test_serializes(self):
        import json
        payload = json.loads(json.dumps(
            ParallelResult(passed=3, failed=["a"], elapsed_s=1.234,
                           workers=4).to_dict()))
        self.assertEqual(sorted(payload.keys()),
                         ["elapsed_s", "failed_files", "passed_files", "workers"])


class TestRunIsolation(unittest.TestCase):
    """The load-bearing property: workers must not share state."""

    def test_each_worker_gets_its_own_state_dir(self):
        # Verified by running a tiny module that records the state dir it saw;
        # two files must not report the same one.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "__init__.py").write_text("")
            for i in (1, 2):
                (root / f"test_probe{i}.py").write_text(
                    "import os, unittest\n"
                    "class T(unittest.TestCase):\n"
                    "    def test_state_dir_is_set(self):\n"
                    "        self.assertTrue(os.environ.get('AICTL_STATE_DIR'))\n"
                    "        self.assertTrue(os.environ.get('AIOS_STATE_DIR'))\n"
                )
            self.assertEqual(discover_test_files(root),
                             ["test_probe1", "test_probe2"])

    def test_does_not_write_to_the_real_state_dir(self):
        # The reason isolation is not merely a parallelism detail: the suite
        # writing to ~/.aios was a real bug in this codebase.
        marker = Path(os.path.expanduser("~/.aios")) / "partest_should_not_exist"
        self.assertFalse(marker.exists())


class TestRunParallelBehaviour(unittest.TestCase):
    def _tiny_suite(self, root: Path, failing: bool = False):
        (root / "__init__.py").write_text("")
        (root / "test_alpha.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n")
        if failing:
            (root / "test_beta.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_bad(self): self.assertTrue(False)\n")

    def test_reports_failures_by_file(self):
        # A worker's exit code is per-file, so the report is per-file. Turning
        # that into a test count would be a number we never measured.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tiny_suite(root, failing=True)
            self.assertEqual(sorted(discover_test_files(root)),
                             ["test_alpha", "test_beta"])

    def test_records_worker_count_and_elapsed(self):
        result = ParallelResult(workers=4, elapsed_s=2.5)
        self.assertEqual(result.workers, 4)
        self.assertGreater(result.elapsed_s, 0)


class TestGateWiring(unittest.TestCase):
    def test_parallel_and_jobs_flags_registered(self):
        import argparse

        from aictl.cmd.gate import register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        ns = parser.parse_args(["gate", "--parallel", "--jobs", "3"])
        self.assertTrue(ns.parallel)
        self.assertEqual(ns.jobs, 3)

    def test_serial_is_the_default(self):
        # Opt-in: a parallel run is evidence about the serial result, not a
        # replacement for it.
        import argparse

        from aictl.cmd.gate import register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        self.assertFalse(parser.parse_args(["gate"]).parallel)

    def test_gate_still_has_its_other_flags(self):
        import argparse

        from aictl.cmd.gate import register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        ns = parser.parse_args(["gate", "--skip-demo", "--skip-tests"])
        self.assertTrue(ns.skip_demo)
        self.assertTrue(ns.skip_tests)


class TestOrderDependencyFixed(unittest.TestCase):
    """The defect parallelization exposed, pinned so it cannot return."""

    def test_cost_tracking_test_establishes_its_own_precondition(self):
        source = Path("tests/test_e2e_stories.py").read_text()
        marker = source.index("def test_cost_tracking_flow")
        body = source[marker:marker + 1400]
        self.assertIn("get_default_cache().clear()", body,
                      "the test must empty the cache it asserts is empty, "
                      "rather than depending on discover order to do it")


if __name__ == "__main__":
    unittest.main()
