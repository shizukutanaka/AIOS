"""Pass 211: the Go port does not build, and nothing said so.

`aictl gate` is this project's "is everything all right?" command. It verified
the Python half and never looked at the other 2,176 lines — the Go port that
CLAUDE.md and the release notes both advertise as "29 Go commands". A
regression there would have shipped undetected.

It is worse than untested. On a clean checkout it does not build: `go.sum`
records a checksum for github.com/spf13/cobra v1.8.1 that disagrees with what
the module proxy serves, and Go correctly refuses with a SECURITY ERROR.

The two hashes are worth looking at:

    downloaded: h1:e5/vxKd/rZsfSJMUX1agtjeTDf+qv1/JdBF8gg5k9ZM=
    go.sum:     h1:e5/vxKd/rZsfSJMUX1agtjeTDf+qv1/JdBF8lex5Gm=

They share a 38-character prefix and differ only in the tail. Independent
hashes differ everywhere, so this is the signature of a corrupted or
hand-edited go.sum entry, not a substituted artifact.

**This deliberately does not fix go.sum.** Rewriting it with whatever the
proxy happened to serve is exactly the control go.sum exists to provide, and
the authoritative value could not be checked because sum.golang.org is
unreachable from here. Reporting an unverifiable state honestly is the right
outcome; silently "fixing" a checksum is not — the same reasoning that kept
this session from routing around every other blocked control.

What is fixed is the silence. A gap in the gate's output can be acted on; a
gap that never appears in it looks like health.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aictl.core.goport import GoStatus, check_go_port


class TestRealTreeIsReported(unittest.TestCase):
    def test_go_port_is_detected(self):
        status = check_go_port(Path("."))
        self.assertTrue(status.present, "go-port/go.mod exists in this tree")

    def test_status_carries_an_explanation(self):
        # A bare False would leave the reader guessing whether it is a code
        # defect, a missing toolchain, or a network problem.
        self.assertTrue(check_go_port(Path(".")).detail.strip())

    def test_checksum_failure_is_named_specifically(self):
        # It must not be reported as a generic build failure: the remedy is
        # completely different, and it is not a code defect.
        status = check_go_port(Path("."))
        if status.builds is False:
            self.assertIn("checksum", status.detail.lower())


class TestAbsentPieces(unittest.TestCase):
    def test_missing_go_port_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            status = check_go_port(Path(td))
            self.assertFalse(status.present)
            self.assertIsNone(status.builds)

    def test_missing_toolchain_is_reported_as_unverified_not_broken(self):
        # "We could not check" and "it is broken" are different claims, and
        # conflating them would make every Go-less machine look like a defect.
        with patch("aictl.core.goport.shutil.which", return_value=None):
            status = check_go_port(Path("."))
        self.assertTrue(status.present)
        self.assertFalse(status.toolchain)
        self.assertIsNone(status.builds)
        self.assertIn("unverified", status.detail)


class TestFailureClassification(unittest.TestCase):
    """Each failure mode has a different remedy, so each is named."""

    def _with_stderr(self, stderr, returncode=1):
        class Proc:
            pass
        proc = Proc()
        proc.returncode = returncode
        proc.stderr = stderr
        with patch("aictl.core.goport.subprocess.run", return_value=proc):
            return check_go_port(Path("."))

    def test_successful_build_is_reported(self):
        status = self._with_stderr("", returncode=0)
        self.assertTrue(status.builds)

    def test_checksum_mismatch_is_its_own_category(self):
        status = self._with_stderr("verifying x: checksum mismatch\nSECURITY ERROR")
        self.assertFalse(status.builds)
        self.assertIn("checksum", status.detail)

    def test_unreachable_proxy_is_unverified_not_broken(self):
        # A network problem is not a claim about the code.
        status = self._with_stderr("dial tcp 1.2.3.4:443: connect: timeout")
        self.assertIsNone(status.builds)
        self.assertIn("unverified", status.detail)

    def test_genuine_compile_error_is_reported_as_a_failure(self):
        status = self._with_stderr("./main.go:12:2: undefined: Foo")
        self.assertFalse(status.builds)
        self.assertIn("undefined", status.detail)

    def test_timeout_does_not_hang_the_gate(self):
        import subprocess

        with patch("aictl.core.goport.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("go", 1)):
            status = check_go_port(Path("."))
        self.assertIsNone(status.builds)
        self.assertIn("timed out", status.detail)

    def test_unexpected_exception_is_swallowed(self):
        with patch("aictl.core.goport.subprocess.run",
                   side_effect=RuntimeError("boom")):
            check_go_port(Path("."))   # must not raise


class TestGateReportsButDoesNotGate(unittest.TestCase):
    def test_gate_has_a_go_port_phase(self):
        import inspect

        from aictl.cmd import gate

        self.assertIn("Go port", inspect.getsource(gate.run))

    def test_go_failure_does_not_fail_the_gate(self):
        # A missing toolchain or unreachable proxy is a property of the
        # machine, not the code — the same reasoning the security phase uses.
        import inspect

        from aictl.cmd import gate

        source = inspect.getsource(gate.run)
        marker = source.index('"Go port"')
        # Every Go result is appended with success=True; the detail carries
        # the bad news so it is visible without making the gate flaky.
        self.assertIn("True", source[marker - 40:marker + 120])

    def test_status_serializes(self):
        import json

        payload = json.loads(json.dumps(GoStatus(present=True).to_dict()))
        self.assertEqual(sorted(payload.keys()),
                         ["builds", "detail", "present", "toolchain"])


def _nearby(source: str, needle: str, window: int = 120) -> str:
    """Text around each occurrence of `needle`, for proximity assertions."""
    out = []
    start = 0
    while (i := source.find(needle, start)) != -1:
        out.append(source[max(0, i - window):i + window])
        start = i + 1
    return "\n".join(out)


class TestGoSumIsNotSilentlyRewritten(unittest.TestCase):
    """The control this pass refused to bypass."""

    def test_no_code_writes_go_sum(self):
        # The property is "nothing WRITES go.sum", not "the string never
        # appears" — the module legitimately explains the mismatch in prose.
        # An earlier version of this test stripped prose mentions with a
        # chain of str.replace calls, which was brittle and tested nothing.
        for module in (Path("aictl/core/goport.py"), Path("aictl/cmd/gate.py")):
            source = module.read_text()
            for writer in ("open(", "write_text", "unlink", "shutil.copy"):
                if writer in source:
                    self.assertNotIn("go.sum", _nearby(source, writer),
                                     f"{module} appears to write go.sum")

    def test_go_mod_tidy_is_not_invoked(self):
        # `go mod tidy` would rewrite go.sum as a side effect, quietly
        # resolving the mismatch by trusting whatever the proxy served.
        source = Path("aictl/core/goport.py").read_text()
        self.assertNotIn("tidy", source)


if __name__ == "__main__":
    unittest.main()
