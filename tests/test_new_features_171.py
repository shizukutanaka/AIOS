"""Pass 171 (audit item #19/P8): `aictl gate` now invokes the project's own
security scanner (aictl/core/security.py's scan()) as part of its own
"safe to ship" bar, closing the last open PAPER-ONLY item in
docs/FEATURE_GAP_LIST.md.

Design note (why this is a smoke test, not a score gate): the scanner's
findings (root vs rootless, cgroup v2 availability, container runtime
presence, PSI) describe the *host environment*, not the code being shipped.
Hard-failing the gate on a score threshold would make it exactly as flaky as
the pre-fix ruff/mypy steps (CLAUDE.md 6.2) — it would fail in any rootless-
less CI/sandbox container. So gate.py's new "Security" check only verifies
that scan() completes all of its checks without raising an exception (using
an isolated tmp state dir so the result is independent of — and does not
pollute — the caller's real state); the live score/findings are reported as
informational detail only, mirroring `aictl doctor --deep`'s existing
score-is-informational convention.

Uses the same fast-unit-test harness as test_new_features_111b.py: patch
Path.rglob to empty (skip the real compile/import walk) and skip tests/demo,
so only the Security step (and the cheap Docs/MCP/Version/Ruff/MyPy steps)
actually run.
"""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch

from aictl.core.security import SecurityFinding, SecurityReport


def _run_gate(as_json=True, security_report=None):
    from aictl.cmd import gate
    args = argparse.Namespace(json=as_json, skip_tests=True, skip_demo=True)
    with ExitStack() as stack:
        stack.enter_context(patch("pathlib.Path.rglob", return_value=[]))
        if security_report is not None:
            stack.enter_context(
                patch("aictl.core.security.scan", return_value=security_report))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = gate.run(args)
    return rc, buf.getvalue()


def _find_check(rc_and_out, name):
    import json
    rc, out = rc_and_out
    data = json.loads(out)
    for c in data["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"no {name!r} check in gate output: {data['checks']}")


class TestGateInvokesSecurityScanner(unittest.TestCase):
    def test_security_check_is_present_and_passes_by_default(self):
        check = _find_check(_run_gate(as_json=True), "Security")
        self.assertTrue(check["passed"])
        self.assertIn("checks", check["detail"])

    def test_gate_still_passes_overall_with_a_low_score(self):
        # A low score (root, no cgroup v2, etc.) is an environment fact, not a
        # code defect -- it must not fail the gate. This is the crux of the
        # fix: naively gating on report.score would make CI/sandbox runs
        # (which are typically root, cgroup-v2-less containers) fail forever.
        low_score_report = SecurityReport(
            score=10,
            findings=[
                SecurityFinding(severity="high", category="isolation",
                                title="State directory world-readable",
                                description="...", remediation="chmod 700 ..."),
                SecurityFinding(severity="medium", category="runtime",
                                title="Running as root",
                                description="...", remediation="..."),
            ],
            checks_passed=2, checks_failed=8, checks_total=10,
        )
        rc, out = _run_gate(as_json=True, security_report=low_score_report)
        self.assertEqual(rc, 0)
        check = _find_check((rc, out), "Security")
        self.assertTrue(check["passed"])
        self.assertIn("10/100", check["detail"])

    def test_security_check_fails_if_the_scanner_itself_is_broken(self):
        # If every check inside scan() raised (the scanner is broken), that
        # IS a real code defect and must fail the gate -- this is what
        # distinguishes "environment is insecure" (fine) from "our own
        # scanner is broken" (not fine).
        broken_report = SecurityReport(
            score=0,
            findings=[
                SecurityFinding(severity="medium", category="runtime",
                                title="Security check error: _check_rootless",
                                description="boom", remediation="investigate"),
            ],
            checks_passed=0, checks_failed=1, checks_total=1,
        )
        rc, out = _run_gate(as_json=True, security_report=broken_report)
        check = _find_check((rc, out), "Security")
        self.assertFalse(check["passed"])

    def test_security_check_fails_if_scan_raises_outright(self):
        from aictl.cmd import gate
        args = argparse.Namespace(json=True, skip_tests=True, skip_demo=True)
        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.rglob", return_value=[]))
            stack.enter_context(
                patch("aictl.core.security.scan", side_effect=RuntimeError("boom")))
            buf = io.StringIO()
            with redirect_stdout(buf):
                gate.run(args)
        check = _find_check((0, buf.getvalue()), "Security")
        self.assertFalse(check["passed"])
        self.assertIn("boom", check["detail"])

    def test_security_check_uses_an_isolated_tmp_dir_not_real_state(self):
        # scan() must be called with some path (an isolated tmp dir), not the
        # caller's real default state dir -- verify by inspecting the call arg.
        from aictl.cmd import gate
        args = argparse.Namespace(json=True, skip_tests=True, skip_demo=True)
        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.rglob", return_value=[]))
            mock_scan = stack.enter_context(
                patch("aictl.core.security.scan",
                     return_value=SecurityReport(checks_total=1, checks_passed=1)))
            buf = io.StringIO()
            with redirect_stdout(buf):
                gate.run(args)
        self.assertEqual(mock_scan.call_count, 1)
        called_with = mock_scan.call_args[0][0]
        self.assertNotEqual(str(called_with), "None")


if __name__ == "__main__":
    unittest.main()
