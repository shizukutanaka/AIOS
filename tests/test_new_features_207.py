"""Pass 207: Musk steps 3 and 5 — simplify, then automate.

**Step 3, simplify.** The same ~14-line state-isolation setUp/tearDown had been
copy-pasted into ten places across four test files. Duplicating a *tricky*
pattern is worse than duplicating a simple one: ten copies are ten chances to
forget the `AIOS_STATE_DIR` half, or to restore the environment in a way that
leaks when a test fails. Getting it wrong was the direct cause of two defects
here — the suite writing into the developer's real `~/.aios`, and tests that
passed only in discover order. `tests/support.py` makes the correct thing the
easy thing; the four files shed 101 lines for 9.

**Step 5, automate — and only now.** The algorithm puts automate last because
automating a process you do not understand just makes the wrong thing happen
faster. This one earned it: the same three CLAUDE.md numbers were hand-edited
about a dozen times in one session, always with the same `sed`, always after
the same trigger. Nothing checked them, so a miscount would silently ship a
false claim about the project's size — and those numbers are the first thing
any reader sees.

Deliberately split: `check_counts` is pure and cheap so `gate` can run it every
time; `sync_counts` writes. A verification step that silently rewrote files
would be a worse tool than the sed it replaced.

Same pass, same principle: gate's CHANGELOG check hardcoded `"v1.7.0"`, a
literal needing a hand-edit at every bump — and a forgotten edit would leave it
passing against the *previous* release forever. Now derived from `VERSION`.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from aictl.core.docsync import (
    CountMismatch,
    check_counts,
    count_commands,
    count_test_files,
    sync_counts,
)
from tests.support import IsolatedStateTestCase, IsolatedTrackerTestCase


class TestCounting(unittest.TestCase):
    def test_counts_real_test_files(self):
        self.assertGreater(count_test_files(), 200)

    def test_counts_real_commands(self):
        self.assertGreater(count_commands(), 50)

    def test_missing_directories_yield_zero(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(count_test_files(Path(td)), 0)
            self.assertEqual(count_commands(Path(td)), 0)

    def test_dunder_modules_are_not_counted_as_commands(self):
        with tempfile.TemporaryDirectory() as td:
            cmd = Path(td) / "aictl" / "cmd"
            cmd.mkdir(parents=True)
            (cmd / "__init__.py").write_text("")
            (cmd / "real.py").write_text("")
            self.assertEqual(count_commands(Path(td)), 1)


class TestCheckIsPure(unittest.TestCase):
    """A verification step must never rewrite the thing it verifies."""

    def _project(self, td, doc_text):
        root = Path(td)
        (root / "tests").mkdir()
        (root / "tests" / "test_a.py").write_text("")
        (root / "CLAUDE.md").write_text(doc_text)
        return root

    def test_check_does_not_modify_the_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "tests/  999 test files, 5+ tests\n")
            before = (root / "CLAUDE.md").read_text()
            check_counts(root, test_count=7)
            self.assertEqual((root / "CLAUDE.md").read_text(), before)

    def test_detects_a_stale_file_count(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "tests/  999 test files\n")
            problems = check_counts(root)
            # The label names the document, so a finding says which file lies.
            self.assertTrue(any(p.label.endswith("test files") for p in problems))
            self.assertTrue(any("CLAUDE.md" in p.label for p in problems))

    def test_accurate_counts_report_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "tests/  1 test files, 7+ tests\n")
            self.assertEqual(check_counts(root, test_count=7), [])

    def test_missing_doc_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(check_counts(Path(td)), [])

    def test_unsupplied_test_count_skips_test_claims(self):
        # Comparing against a number nobody measured would be worse than not
        # checking: it would report a mismatch that means nothing.
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "tests/  1 test files, 9999+ tests\n")
            self.assertEqual(check_counts(root, test_count=0), [])

    def test_mismatch_message_names_both_numbers(self):
        mismatch = CountMismatch("tests", documented=10, actual=12)
        self.assertIn("10", str(mismatch))
        self.assertIn("12", str(mismatch))

    def test_message_does_not_hardcode_a_document_name(self):
        # It previously said "CLAUDE.md says ...", which mislabelled every
        # RELEASE.md finding as coming from the wrong file.
        message = str(CountMismatch("RELEASE.md tests", documented=1, actual=2))
        self.assertIn("RELEASE.md", message)
        self.assertNotIn("CLAUDE.md", message)


class TestReleaseNotesAreTracked(unittest.TestCase):
    """RELEASE.md matters more than CLAUDE.md: it becomes the public release
    announcement, so a stale number there is a false claim shipped to everyone."""

    def _project(self, td, claude, release):
        root = Path(td)
        (root / "tests").mkdir()
        (root / "tests" / "test_a.py").write_text("")
        (root / "CLAUDE.md").write_text(claude)
        (root / "RELEASE.md").write_text(release)
        return root

    def test_stale_release_notes_are_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "1 test files, 7+ tests\n", "- **99+ tests**\n")
            problems = check_counts(root, test_count=7)
            self.assertTrue(any("RELEASE.md" in p.label for p in problems))

    def test_thousands_separator_is_understood(self):
        # RELEASE.md writes "3,783+ tests"; a checker that only understood
        # "3783+ tests" would silently pass a stale grouped number.
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "1 test files\n", "- **3,519+ tests**\n")
            problems = check_counts(root, test_count=3783)
            self.assertTrue(any("RELEASE.md" in p.label for p in problems))

    def test_sync_preserves_each_documents_number_style(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "1 test files, 5+ tests\n", "- **5+ tests**\n")
            sync_counts(root, test_count=3783)
            self.assertIn("3783+ tests", (root / "CLAUDE.md").read_text())
            self.assertIn("3783+ tests", (root / "RELEASE.md").read_text())

    def test_grouped_number_stays_grouped(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "1 test files\n", "- **3,519+ tests**\n")
            sync_counts(root, test_count=3783)
            self.assertIn("3,783+ tests", (root / "RELEASE.md").read_text())


class TestSyncWrites(unittest.TestCase):
    def test_sync_fixes_the_numbers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tests").mkdir()
            for i in range(3):
                (root / "tests" / f"test_{i}.py").write_text("")
            (root / "CLAUDE.md").write_text("tests/  999 test files, 5+ tests\n")
            changed = sync_counts(root, test_count=42)
            self.assertTrue(changed)
            text = (root / "CLAUDE.md").read_text()
            self.assertIn("3 test files", text)
            self.assertIn("42+ tests", text)
            self.assertEqual(check_counts(root, test_count=42), [])

    def test_sync_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tests").mkdir()
            (root / "tests" / "test_a.py").write_text("")
            (root / "CLAUDE.md").write_text("1 test files\n")
            self.assertEqual(sync_counts(root), [])


class TestGateIntegration(unittest.TestCase):
    def test_gate_has_a_counts_phase(self):
        import inspect

        from aictl.cmd import gate

        self.assertIn("Counts", inspect.getsource(gate.run))

    def test_changelog_check_is_derived_not_hardcoded(self):
        # A literal version here needs a hand-edit at every bump, and a
        # forgotten edit leaves the check passing against the old release.
        #
        # The check moved out of run() into gate._docs_issues when the whole
        # Docs phase became derived, so this reads the helper rather than the
        # caller. Asserting on the *behaviour* as well as the source: a
        # CHANGELOG naming only some other version must be reported.
        import inspect
        import tempfile

        from aictl.__main__ import VERSION
        from aictl.cmd import gate

        source = inspect.getsource(gate._docs_issues)
        self.assertIn("expected_release", source)
        self.assertNotIn('"v1.7.0" in cl_text', source)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("```\naictl doctor\n```\n")
            (root / "CHANGELOG.md").write_text("## v0.0.1 — ancient\n")
            issues, _ = gate._docs_issues(root)
            self.assertTrue(any(f"v{VERSION}" in i for i in issues),
                            "a stale CHANGELOG must be reported")

    def test_this_repo_is_in_sync(self):
        # The automation checking itself: if this fails, CLAUDE.md is lying.
        self.assertEqual([str(p) for p in check_counts(Path("."))], [])


class TestSharedIsolationSupport(unittest.TestCase):
    """Step 3's extraction, verified rather than assumed."""

    def test_both_env_names_are_set(self):
        # Setting only one leaves some module pointed at the real state dir —
        # the exact bug this exists to prevent.
        class Probe(IsolatedStateTestCase):
            def runTest(self):
                assert os.environ["AICTL_STATE_DIR"] == os.environ["AIOS_STATE_DIR"]
                assert Path(os.environ["AICTL_STATE_DIR"]).is_dir()

        result = Probe().run()
        self.assertTrue(result.wasSuccessful())

    def test_environment_is_restored_afterwards(self):
        before = (os.environ.get("AICTL_STATE_DIR"), os.environ.get("AIOS_STATE_DIR"))

        class Probe(IsolatedStateTestCase):
            def runTest(self):
                pass

        Probe().run()
        self.assertEqual(
            (os.environ.get("AICTL_STATE_DIR"), os.environ.get("AIOS_STATE_DIR")),
            before)

    def test_environment_is_restored_even_when_a_test_fails(self):
        # Registered via addCleanup rather than tearDown precisely for this.
        before = os.environ.get("AICTL_STATE_DIR")

        class Failing(IsolatedStateTestCase):
            def runTest(self):
                self.fail("deliberate")

        Failing().run()
        self.assertEqual(os.environ.get("AICTL_STATE_DIR"), before)

    def test_tracker_variant_restores_persistence_flag(self):
        from aictl.runtime.prefix_route import get_default_tracker

        before = get_default_tracker().persistence_enabled()

        class Probe(IsolatedTrackerTestCase):
            def runTest(self):
                get_default_tracker().enable_persistence(True)

        Probe().run()
        self.assertEqual(get_default_tracker().persistence_enabled(), before)

    def test_duplicated_boilerplate_is_gone(self):
        # The four files that carried ten copies must now share one.
        for name in ("192", "193", "195", "196"):
            source = Path(f"tests/test_new_features_{name}.py").read_text()
            self.assertNotIn('os.environ["AICTL_STATE_DIR"] =', source,
                             f"{name} still hand-rolls state isolation")


if __name__ == "__main__":
    unittest.main()
