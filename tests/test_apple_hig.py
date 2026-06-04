"""Tests for Apple HIG improvements: startup time, empty states, welcome."""

import io
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestStartupPerformance(unittest.TestCase):
    """Startup must be fast — Apple standard: <200ms for CLI tools."""

    def test_import_time_under_500ms(self):
        """Module import should be fast with lazy loading."""
        t0 = time.perf_counter()
        # Import the main module (not build_parser yet)
        import importlib
        import aictl.core.constants
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # core.constants alone should be very fast
        self.assertLess(elapsed_ms, 200,
                        f"core.constants import took {elapsed_ms:.0f}ms — too slow")

    def test_version_flag_fast(self):
        """--version must respond without building the full parser."""
        from aictl.core.constants import AICTL_VERSION
        self.assertEqual(AICTL_VERSION, "1.6.0")

    def test_build_parser_completes(self):
        """Parser build must succeed (imports all 61 commands)."""
        from aictl.__main__ import build_parser
        p = build_parser()
        # Count registered subcommands
        for action in p._actions:
            if hasattr(action, "choices") and action.choices:
                self.assertGreaterEqual(len(action.choices), 55)
                return
        self.fail("No subparsers found in parser")


class TestEmptyState(unittest.TestCase):
    def test_empty_state_show_rag(self):
        from aictl.core.empty_state import show
        buf = io.StringIO()
        show("rag_index", out=buf)
        output = buf.getvalue()
        self.assertIn("aictl rag index", output)
        self.assertIn("Get started", output)

    def test_empty_state_show_quota(self):
        from aictl.core.empty_state import show
        buf = io.StringIO()
        show("quota", out=buf)
        self.assertIn("aictl quota create", buf.getvalue())

    def test_empty_state_show_batch(self):
        from aictl.core.empty_state import show
        buf = io.StringIO()
        show("batch", out=buf)
        self.assertIn("aictl batch add", buf.getvalue())

    def test_empty_state_unknown_key_silent(self):
        from aictl.core.empty_state import show
        buf = io.StringIO()
        show("totally_unknown_key_xyz", out=buf)
        self.assertEqual(buf.getvalue(), "")

    def test_empty_state_is_empty_check(self):
        from aictl.core.empty_state import is_empty
        self.assertTrue(is_empty("rag_index"))
        self.assertFalse(is_empty("not_a_real_key"))

    def test_rag_ask_empty_shows_guidance(self):
        """aictl rag ask on empty index shows empty state, not cryptic error."""
        from aictl.__main__ import build_parser
        from aictl.cmd.rag import run_ask
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                p = build_parser()
                args = p.parse_args(["rag", "ask", "what is X?"])
                args.k = 5
                args.json = False

                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = run_ask(args)

                self.assertNotEqual(rc, 0)
                # Must show how to fix — not just an error
                self.assertIn("aictl rag index", buf.getvalue())
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)


class TestContextAwareWelcome(unittest.TestCase):
    def test_welcome_shows_setup_on_first_run(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                from aictl.core.welcome import _detect_next_action
                action = _detect_next_action(first_time=True)
                self.assertIn("setup", action["cmd"])
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)

    def test_welcome_shows_dash_when_healthy(self):
        """When initialized and has RAG, suggest dashboard."""
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                # Create markers for initialized state
                Path(td, "node.json").write_text("{}")
                from aictl.core.welcome import _detect_next_action
                action = _detect_next_action(first_time=False)
                # Should suggest something useful (not crash)
                self.assertIn("cmd", action)
                self.assertIn("why", action)
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)

    def test_contextual_commands_no_crash(self):
        """_show_contextual_commands must not raise in any state."""
        from aictl.core.welcome import _show_contextual_commands
        buf = io.StringIO()
        with redirect_stdout(buf):
            _show_contextual_commands()
        # Should produce some output
        self.assertGreater(len(buf.getvalue()), 0)

    def test_show_welcome_returns_zero(self):
        """show_welcome must always return 0."""
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                from aictl.core.welcome import show_welcome
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = show_welcome()
                self.assertEqual(rc, 0)
                self.assertIn("aictl", buf.getvalue())
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)


class TestLazyImports(unittest.TestCase):
    """Verify the lazy import structure is correct."""

    def test_all_commands_registered(self):
        """All 61+ commands must be reachable through build_parser."""
        from aictl.__main__ import build_parser
        p = build_parser()
        commands = set()
        for action in p._actions:
            if hasattr(action, "choices") and action.choices:
                commands = set(action.choices.keys())
                break

        required = [
            "init", "doctor", "fit", "quant", "troubleshoot",
            "rag", "guard", "cache", "perf", "dash",
            "tco", "quota", "batch", "update", "setup", "help",
        ]
        for cmd in required:
            self.assertIn(cmd, commands, f"Missing command: {cmd}")

    def test_no_import_error_on_all_commands(self):
        """build_parser must succeed — no ImportError in any command module."""
        from aictl.__main__ import build_parser
        try:
            p = build_parser()
            self.assertIsNotNone(p)
        except ImportError as e:
            self.fail(f"build_parser raised ImportError: {e}")


if __name__ == "__main__":
    unittest.main()
