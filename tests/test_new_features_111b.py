"""Pass 111 (loop, cont.): exit-code honesty for `aictl gate --json`.

Same class as the security fix: `aictl gate --json` is the canonical CI machine
gate, but its --json branch returned 0 unconditionally — so a failed gate (the
JSON even said "passed": false) still exited 0 and CI never caught it. The JSON
exit code now mirrors the human path (`0 if all_pass else 1`).

The compile/import loops are neutralized by patching Path.rglob to empty (0
errors, instant) and tests/demo are skipped, so this stays a fast unit test. A
forced Docs failure (empty help TOPICS) drives all_pass False to exercise the
non-zero branch.
"""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch


def _run_gate(as_json, force_fail):
    from aictl.cmd import gate
    args = argparse.Namespace(json=as_json, skip_tests=True, skip_demo=True)
    with ExitStack() as stack:
        # Instant compile/import loops — we are testing the exit-code wiring,
        # not the checks themselves.
        stack.enter_context(patch("pathlib.Path.rglob", return_value=[]))
        if force_fail:
            # Empty help topics -> all critical commands "missing" -> Docs fails.
            stack.enter_context(patch("aictl.cmd.help.TOPICS", {}))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = gate.run(args)
    return rc


class TestGateExitCodeHonesty(unittest.TestCase):
    def test_failed_gate_is_nonzero_in_json_mode(self):
        # The bug: this returned 0 even though the gate failed.
        self.assertEqual(_run_gate(as_json=True, force_fail=True), 1)

    def test_failed_gate_is_nonzero_in_human_mode(self):
        self.assertEqual(_run_gate(as_json=False, force_fail=True), 1)

    def test_passing_gate_is_zero_in_both_modes(self):
        self.assertEqual(_run_gate(as_json=True, force_fail=False), 0)
        self.assertEqual(_run_gate(as_json=False, force_fail=False), 0)


if __name__ == "__main__":
    unittest.main()
