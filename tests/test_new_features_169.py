"""Pass 169 (audit P9): Go port apply/down must stop reporting false success.

FEATURE_GAP_AUDIT.md's last remaining item: go-port/cmd/aictl/main.go's
cmdApply/cmdDown printed a leading "✓ Applying stack..."/"✓ Stopping stack..."
and returned exit 0 while doing nothing — worse than a missing feature (a
caller checking only the exit code believes a real infrastructure operation
succeeded). Fix: no leading success text, RunE now returns a non-nil error
(cobra prints "Error: ..." to stderr and main() exits 1 — the same convention
this file already uses for cmdApply's own "--file/-f required" validation
error), and the message points at the Python CLI which can actually do this.
--json mode still emits a parseable body (status: "not_implemented") but the
function still returns the error afterward, so the exit code is non-zero
regardless of output mode.

`go build`/`go test` cannot run in this sandbox (a module-checksum mismatch
for github.com/spf13/cobra — a security-relevant failure not bypassed), so
this is a source-inspection regression test: it asserts the specific false-
success pattern is gone and the honest pattern is present, using the same
technique the pre-existing tests/test_category_audit_fixes_18.py already uses
for this exact file. `gofmt -l`/`-d` (no network required) confirmed the
edited file parses as syntactically valid Go with no diff in the touched
functions.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_MAIN_GO = Path(__file__).parent.parent / "go-port" / "cmd" / "aictl" / "main.go"


def _func_body(name: str) -> str:
    src = _MAIN_GO.read_text()
    m = re.search(rf"func {name}\(\).*?^}}", src, re.MULTILINE | re.DOTALL)
    assert m is not None, f"{name} function must exist in main.go"
    return m.group(0)


def _strip_line_comments(go_src: str) -> str:
    """Remove '// ...' line comments so a source-inspection test can't be
    fooled by (or accidentally fail on) prose that mentions old code inside
    an explanatory comment — only checks executable code."""
    return "\n".join(re.sub(r"//.*$", "", line) for line in go_src.splitlines())


class TestNoFalseSuccessInApply(unittest.TestCase):
    def test_no_leading_success_checkmark_before_json_check(self):
        code = _strip_line_comments(_func_body("cmdApply"))
        # The old bug: an unconditional "✓ Applying stack..." printed before
        # any error/status check, giving the appearance of success no matter
        # what. There must be no bare success-checkmark Printf left over in
        # the EXECUTABLE code (an explanatory comment mentioning the old
        # behavior for future readers is fine and expected).
        self.assertNotIn("✓ Applying stack", code)

    def test_returns_a_non_nil_error_for_the_unimplemented_path(self):
        body = _func_body("cmdApply")
        # The whole point: the RunE closure must not `return nil` after the
        # not-yet-implemented branch. It must return an error so cobra sets a
        # non-zero exit code (see main()'s `if err := root.Execute(); err !=
        # nil { os.Exit(1) }`).
        self.assertIn("not implemented in the Go port", body)
        # After the --file/-f validation check, the remaining reachable code
        # must not contain a bare `return nil`.
        after_validation = body.split('return fmt.Errorf("--file/-f required")', 1)[1]
        self.assertNotIn("return nil", after_validation)

    def test_json_status_is_honestly_labeled(self):
        body = _func_body("cmdApply")
        self.assertIn('"not_implemented"', body)
        self.assertNotIn('"status": "stub"', body)

    def test_points_at_the_python_cli(self):
        body = _func_body("cmdApply")
        self.assertIn("python3 -m aictl apply", body)

    def test_json_error_still_propagates(self):
        body = _func_body("cmdApply")
        # printJSON's own error must not be swallowed (matches this file's
        # own established `if err := f(); err != nil { return err }` idiom,
        # e.g. already used at "if err := store.SaveNode(ns); err != nil").
        self.assertIn("if err := printJSON(", body)


class TestNoFalseSuccessInDown(unittest.TestCase):
    def test_no_leading_success_checkmark(self):
        code = _strip_line_comments(_func_body("cmdDown"))
        self.assertNotIn("✓ Stopping stack", code)

    def test_returns_a_non_nil_error(self):
        body = _func_body("cmdDown")
        code = _strip_line_comments(body)
        self.assertIn("not implemented in the Go port", body)
        self.assertNotIn("return nil", code)

    def test_json_status_is_honestly_labeled(self):
        body = _func_body("cmdDown")
        self.assertIn('"not_implemented"', body)
        self.assertNotIn('"status": "stopping"', body)

    def test_points_at_the_python_cli(self):
        body = _func_body("cmdDown")
        self.assertIn("python3 -m aictl down", body)


class TestExistingConventionsPreserved(unittest.TestCase):
    """The pre-existing test_category_audit_fixes_18.py checks jsonFlag/
    printJSON are still referenced by cmdApply — verifying that guard survives
    this rewrite too (belt-and-suspenders alongside running that file itself)."""

    def test_apply_still_checks_json_flag(self):
        self.assertIn("jsonFlag", _func_body("cmdApply"))

    def test_apply_json_path_still_calls_printJSON(self):
        self.assertIn("printJSON", _func_body("cmdApply"))

    def test_apply_still_validates_missing_file(self):
        self.assertIn('--file/-f required', _func_body("cmdApply"))

    def test_down_still_requires_exactly_one_arg(self):
        self.assertIn("cobra.ExactArgs(1)", _func_body("cmdDown"))


if __name__ == "__main__":
    unittest.main()
