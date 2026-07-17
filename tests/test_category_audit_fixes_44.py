"""Pass 44 regression tests: mcp_server version constant, eval double-run, baseline KeyError."""

import pathlib
import unittest


class TestMcpServerVersionConstant(unittest.TestCase):
    """mcp_server.py must use AICTL_VERSION constant, not a hardcoded string."""

    def test_server_version_not_hardcoded(self):
        """SERVER_VERSION must not be assigned the literal string '1.6.0'."""
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "mcp_server.py").read_text()
        self.assertNotIn(
            'SERVER_VERSION = "1.6.0"',
            src,
            "mcp_server.py must not hardcode SERVER_VERSION; use AICTL_VERSION",
        )

    def test_server_version_uses_aictl_version(self):
        """mcp_server.py must import and use AICTL_VERSION for SERVER_VERSION."""
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "mcp_server.py").read_text()
        self.assertIn("AICTL_VERSION", src,
                      "mcp_server.py must import AICTL_VERSION from aictl.core.constants")
        self.assertIn("SERVER_VERSION = AICTL_VERSION", src,
                      "mcp_server.py must assign SERVER_VERSION = AICTL_VERSION")

    def test_server_version_matches_constant(self):
        """SERVER_VERSION at runtime must equal AICTL_VERSION."""
        import aictl.mcp_server as ms
        from aictl.core.constants import AICTL_VERSION
        self.assertEqual(ms.SERVER_VERSION, AICTL_VERSION,
                         "SERVER_VERSION must equal AICTL_VERSION at runtime")


class TestEvalNoDoubleRun(unittest.TestCase):
    """eval compare must not run eval cases twice (double-run was a dead code bug)."""

    def test_compare_source_no_redirect_stdout(self):
        """run_compare must not contain the dead redirect_stdout + buf.getvalue() block."""
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "eval.py").read_text()
        fn_start = src.find("def run_compare(")
        self.assertNotEqual(fn_start, -1, "run_compare not found in eval.py")
        next_fn = src.find("\ndef ", fn_start + 1)
        fn_body = src[fn_start:next_fn] if next_fn != -1 else src[fn_start:]
        self.assertNotIn(
            "buf.getvalue()",
            fn_body,
            "run_compare must not contain dead buf.getvalue() expression",
        )

    def test_compare_source_no_discarded_redirect(self):
        """run_compare must not silently discard redirect_stdout output."""
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "eval.py").read_text()
        fn_start = src.find("def run_compare(")
        next_fn = src.find("\ndef ", fn_start + 1)
        fn_body = src[fn_start:next_fn] if next_fn != -1 else src[fn_start:]
        self.assertNotIn(
            "redirect_stdout",
            fn_body,
            "run_compare must not have a redirect_stdout that discards its output",
        )


class TestEvalBaselineIdFilter(unittest.TestCase):
    """eval compare must not KeyError on baseline cases missing 'id' key."""

    def test_base_by_id_filters_missing_id(self):
        """baseline dict comprehension must skip cases without 'id' key."""
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "eval.py").read_text()
        self.assertIn(
            'if "id" in c',
            src,
            "eval.py compare must filter cases missing 'id' key to avoid KeyError",
        )

    def test_curr_by_id_filters_missing_id(self):
        """current dict comprehension must also skip cases without 'id' key."""
        import re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "eval.py").read_text()
        matches = re.findall(r'c\["id"\].*if "id" in c', src)
        self.assertGreaterEqual(
            len(matches), 2,
            "eval.py must filter 'id' in both base_by_id and curr_by_id",
        )


if __name__ == "__main__":
    unittest.main()
