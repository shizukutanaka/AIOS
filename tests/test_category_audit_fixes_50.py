"""Pass 50 regression tests: gate.py resource leak and Ruff error-count type."""

import io
import pathlib
import unittest


class TestGateTestRunnerStream(unittest.TestCase):
    """gate.py test runner must not open /dev/null (file descriptor leak)."""

    def test_gate_source_does_not_open_devnull(self):
        """gate.py must use io.StringIO(), not open(os.devnull) for the stream."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "cmd" / "gate.py").read_text()
        self.assertNotIn(
            "open(os.devnull",
            src,
            "gate.py must use io.StringIO() instead of open(os.devnull) to avoid fd leaks",
        )

    def test_gate_source_uses_stringio(self):
        """gate.py test runner stream must be io.StringIO()."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "cmd" / "gate.py").read_text()
        self.assertIn(
            "io.StringIO()",
            src,
            "gate.py should use io.StringIO() for TextTestRunner stream",
        )

    def test_io_module_imported(self):
        """gate.py must import io at the top level."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "cmd" / "gate.py").read_text()
        self.assertIn("import io\n", src,
                      "gate.py must import io to use io.StringIO()")


class TestGateRuffErrorCount(unittest.TestCase):
    """Ruff error-count extraction must always produce an integer."""

    def test_ruff_error_count_always_int(self):
        """The n variable computed from ruff output must be an integer."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "cmd" / "gate.py").read_text()
        # The old buggy pattern returned strings; the new one uses len(lines)
        self.assertNotIn(
            "lines[-1] if lines else",
            src,
            "Ruff error count must not fall back to a string value",
        )

    def test_ruff_n_is_always_int_logic(self):
        """Verify the replacement expression keeps n as an int type."""
        # Simulate what gate.py does on ruff failure
        # old: n = proc.stdout.count("\n--> ") or (lines[-1] if lines else "errors")
        # new: n = proc.stdout.count("\n--> ") or len(lines)
        stdout = "aictl/cmd/foo.py:42:1: E501 line too long"
        lines = stdout.strip().splitlines()
        n = stdout.count("\n--> ") or len(lines)
        self.assertIsInstance(n, int)
        self.assertEqual(n, 1)  # 1 line in the output

    def test_ruff_empty_output_gives_zero(self):
        """Empty ruff output gives n=0 (not a crash or string)."""
        stdout = ""
        lines = stdout.strip().splitlines()
        n = stdout.count("\n--> ") or len(lines)
        self.assertIsInstance(n, int)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
