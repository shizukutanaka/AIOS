"""Tests for the first-run welcome experience and human error system."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestWelcome(unittest.TestCase):
    def test_first_run_detection(self):
        from aictl.core.welcome import is_first_run
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                # Empty dir → first run
                self.assertTrue(is_first_run())
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)

    def test_marker_persists(self):
        from aictl.core.welcome import is_first_run, mark_welcome_shown
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                self.assertTrue(is_first_run())
                mark_welcome_shown()
                self.assertFalse(is_first_run())
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)

    def test_show_welcome_returns_zero(self):
        from aictl.core.welcome import show_welcome
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                # Capture stdout
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = show_welcome()
                self.assertEqual(rc, 0)
                output = buf.getvalue()
                # Should contain the version and at least one suggested action
                self.assertIn("aictl", output)
                self.assertTrue("→" in output or "Next step" in output or "Quick actions" in output)
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)

    def test_next_action_for_first_run(self):
        from aictl.core.welcome import _detect_next_action
        action = _detect_next_action(first_time=True)
        self.assertIn("cmd", action)
        self.assertIn("why", action)
        # First run should suggest setup (guided 5-step onboarding)
        self.assertIn("setup", action["cmd"])


class TestErrorMessages(unittest.TestCase):
    def test_no_engine_available_has_action(self):
        from aictl.core.errors import NoEngineAvailable
        e = NoEngineAvailable()
        s = str(e)
        self.assertIn("Try this:", s)
        self.assertIn("aictl setup", s)

    def test_model_too_large_includes_numbers(self):
        from aictl.core.errors import ModelTooLarge
        e = ModelTooLarge("llama-70b", "RTX 4090", need_gb=140, have_gb=24)
        s = str(e)
        self.assertIn("llama-70b", s)
        self.assertIn("24", s)
        self.assertIn("140", s)
        self.assertIn("Try this:", s)

    def test_model_not_found(self):
        from aictl.core.errors import ModelNotFound
        e = ModelNotFound("xyz-99b")
        self.assertIn("xyz-99b", str(e))

    def test_format_for_user_handles_value_error(self):
        from aictl.core.errors import format_for_user
        msg = format_for_user(ValueError("internal detail"))
        self.assertIn("Try this:", msg)
        self.assertIn("aictl doctor", msg)

    def test_format_for_user_handles_file_not_found(self):
        from aictl.core.errors import format_for_user
        e = FileNotFoundError(2, "No such file", "/nonexistent")
        msg = format_for_user(e)
        # Either 'File not found' or 'aictl doctor' should appear
        self.assertTrue("/nonexistent" in msg or "doctor" in msg)

    def test_format_for_user_handles_connection_refused(self):
        from aictl.core.errors import format_for_user
        msg = format_for_user(ConnectionRefusedError())
        self.assertIn("aictl status", msg)

    def test_aictlerror_exit_code(self):
        from aictl.core.errors import (
            NoEngineAvailable, ModelTooLarge, OutOfMemory,
        )
        # All standard errors should have a non-zero exit code
        self.assertNotEqual(NoEngineAvailable().exit_code, 0)
        self.assertNotEqual(OutOfMemory().exit_code, 0)


class TestMainFlowIntegration(unittest.TestCase):
    def test_main_with_no_args_shows_welcome(self):
        """Running `aictl` (no args) should print the welcome banner."""
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                with patch.object(sys, "argv", ["aictl"]):
                    from aictl.__main__ import main
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        rc = main()
                    self.assertEqual(rc, 0)
                    self.assertIn("aictl", buf.getvalue())
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)


if __name__ == "__main__":
    unittest.main()
