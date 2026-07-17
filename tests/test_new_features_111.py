"""Pass 111 (loop): exit-code honesty for `aictl security --json`.

The human render of `aictl security` returns a non-zero exit code when the
audit fails (score < 50), so a CI gate can block a deploy on it. The --json
branch, however, returned 0 unconditionally — discarding the failure signal,
so `aictl security --json; echo $?` (the natural machine gate) always passed
even with critical findings. The JSON exit code now mirrors the human path.
"""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from aictl.core.security import SecurityReport, SecurityFinding


def _run(score, as_json):
    from aictl.cmd.security import run
    report = SecurityReport(
        score=score,
        findings=[SecurityFinding(severity="critical", category="auth",
                                  title="t", description="d")] if score < 50 else [],
        checks_passed=1, checks_failed=0, checks_total=1,
    )
    with patch("aictl.cmd.security.scan", return_value=report):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run(argparse.Namespace(state_dir=None, json=as_json))
    return rc


class TestSecurityExitCodeHonesty(unittest.TestCase):
    def test_failing_score_is_nonzero_in_both_modes(self):
        self.assertEqual(_run(20, as_json=False), 1)
        self.assertEqual(_run(20, as_json=True), 1)  # was 0 — the bug

    def test_passing_score_is_zero_in_both_modes(self):
        self.assertEqual(_run(85, as_json=False), 0)
        self.assertEqual(_run(85, as_json=True), 0)

    def test_json_and_human_agree_at_threshold(self):
        # 50 is the human path's pass boundary; both modes must agree.
        self.assertEqual(_run(50, as_json=True), _run(50, as_json=False))
        self.assertEqual(_run(49, as_json=True), _run(49, as_json=False))


if __name__ == "__main__":
    unittest.main()
