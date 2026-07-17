"""Pass 100 (loop, Socratic new perspective): output portability under limited encodings.

New lens: does output survive a non-UTF-8 environment? Under an ASCII stdout
encoding (C locale — minimal containers, cron, some CI), printing a decorative
glyph (✓/✗/—) raised UnicodeEncodeError and killed the command. main() now
hardens stdio (errors='backslashreplace') so glyphs degrade instead of crashing.
Also: UnicodeError is a ValueError subclass, so it had been mislabeled "Invalid
input" by the error classifier — now excluded (it's an environment fault).
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest


class TestStdioHardening(unittest.TestCase):

    def test_backslashreplace_does_not_crash_on_glyph(self):
        # The mechanism _harden_stdio relies on: an ASCII stream with
        # backslashreplace encodes a glyph instead of raising.
        stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        stream.reconfigure(errors="backslashreplace")
        stream.write("status: ✓ ✗ —\n")  # must not raise
        stream.flush()
        self.assertIn(b"\\u2713", stream.buffer.getvalue())

    def test_status_under_ascii_locale_does_not_crash(self):
        # End-to-end: the real CLI under an ASCII encoding must not emit a
        # UnicodeEncodeError / traceback (it may still return non-zero for
        # "not initialized", which is fine).
        env = dict(os.environ, PYTHONIOENCODING="ascii", LC_ALL="C")
        proc = subprocess.run([sys.executable, "-m", "aictl", "status"],
                              capture_output=True, text=True, env=env, timeout=60)
        blob = proc.stdout + proc.stderr
        self.assertNotIn("UnicodeEncodeError", blob)
        self.assertNotIn("Traceback (most recent call last)", blob)


class TestUnicodeErrorNotInputError(unittest.TestCase):

    def test_unicode_error_is_generic_not_input(self):
        from aictl.core.errors import format_for_user
        msg = format_for_user(UnicodeDecodeError("ascii", b"x", 0, 1, "bad"))
        self.assertNotIn("Invalid input", msg)
        self.assertIn("Unexpected error", msg)

    def test_plain_value_error_still_input(self):
        from aictl.core.errors import format_for_user
        self.assertIn("Invalid input", format_for_user(ValueError("hours > 24")))


if __name__ == "__main__":
    unittest.main()
