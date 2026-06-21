"""Pass 131: safe env-var float parsing in cost_per_call (no import-time crash).

Research-informed (調査: Qiita/Zenn — bad config must degrade to a documented
default, never `except: pass` silently and never crash the process).

`cost_per_call` read three tuning knobs at *import time* with a naive
`float(os.environ[...])`. A malformed value crashed the whole CLI/SDK during
import — before the error handler could run — for any command importing it:

    AICTL_GPU_WATTS=abc python3 -c "import aictl.core.cost_per_call"
        → ValueError: could not convert string to float: 'abc'

and a negative value silently produced negative cost estimates. `_env_float`
now: unset/empty → default; non-numeric or below the minimum → warn on stderr
and use the default (never crash, never accept a bad physical quantity). stderr
keeps --json output on stdout clean.
"""

from __future__ import annotations

import importlib
import io
import os
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch


class TestEnvFloat(unittest.TestCase):
    def _fn(self):
        import aictl.core.cost_per_call as c
        return c._env_float

    def test_unset_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X_TEST_KNOB", None)
            self.assertEqual(self._fn()("X_TEST_KNOB", 5.0), 5.0)

    def test_empty_returns_default(self):
        with patch.dict(os.environ, {"X_TEST_KNOB": ""}):
            self.assertEqual(self._fn()("X_TEST_KNOB", 5.0), 5.0)

    def test_valid_override(self):
        with patch.dict(os.environ, {"X_TEST_KNOB": "12.5"}):
            self.assertEqual(self._fn()("X_TEST_KNOB", 5.0), 12.5)

    def test_non_numeric_warns_and_defaults(self):
        with patch.dict(os.environ, {"X_TEST_KNOB": "abc"}):
            buf = io.StringIO()
            with redirect_stderr(buf):
                val = self._fn()("X_TEST_KNOB", 5.0)
        self.assertEqual(val, 5.0)
        self.assertIn("warning", buf.getvalue())

    def test_below_minimum_defaults(self):
        with patch.dict(os.environ, {"X_TEST_KNOB": "-3"}):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(self._fn()("X_TEST_KNOB", 5.0, minimum=0.0), 5.0)

    def test_minimum_inclusive(self):
        with patch.dict(os.environ, {"X_TEST_KNOB": "1"}):
            self.assertEqual(self._fn()("X_TEST_KNOB", 28800.0, minimum=1.0), 1.0)


class TestModuleSurvivesBadEnv(unittest.TestCase):
    def test_reimport_with_bad_value_does_not_raise(self):
        import aictl.core.cost_per_call as c
        with patch.dict(os.environ, {"AICTL_GPU_WATTS": "not-a-number"}):
            with redirect_stderr(io.StringIO()):
                importlib.reload(c)
            self.assertEqual(c._LOCAL_WATTS, 450.0)   # fell back, no crash
        # Restore clean module state for other tests.
        with redirect_stderr(io.StringIO()):
            importlib.reload(c)


if __name__ == "__main__":
    unittest.main()
