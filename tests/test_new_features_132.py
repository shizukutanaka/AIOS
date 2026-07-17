"""Pass 132: invalid log level must fall back to default, not max verbosity.

Research-informed (調査: Qiita/Zenn — the std logging module rejects unknown
level strings; the idiom is to validate against known levels and fall back to a
default; WARNING-as-default examples for env-var-driven log config).

`StructuredLogger._log` filters via
`self._levels.get(level, 0) < self._levels.get(self.level, 0)`. If `self.level`
was invalid (e.g. `AIOS_LOG_LEVEL=warning` instead of `warn`, or any typo),
`_levels.get(self.level, 0)` returned 0 — making the threshold 0, so EVERYTHING
(including debug) was logged. A user trying to *quiet* the logs accidentally got
*maximum* verbosity (opposite of intent), same silent-wrong-behavior class as
the bool/env bugs.

`__init__` now normalizes (strip+lower) and validates the level against the
known set, falling back to the documented default ("info") on anything unknown.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _logger(level):
    from aictl.core.logging import StructuredLogger
    return StructuredLogger("t", log_dir=Path(tempfile.mkdtemp()), level=level)


class TestLogLevelNormalization(unittest.TestCase):
    def test_valid_levels_preserved(self):
        for lvl in ("debug", "info", "warn", "error"):
            self.assertEqual(_logger(lvl).level, lvl)

    def test_case_insensitive(self):
        self.assertEqual(_logger("ERROR").level, "error")
        self.assertEqual(_logger("  Warn ").level, "warn")

    def test_invalid_falls_back_to_info(self):
        # "warning" is a common mistake for "warn"; must not disable filtering.
        for bad in ("warning", "bogus", "", "verbose", "critical"):
            self.assertEqual(_logger(bad).level, "info", bad)

    def test_invalid_level_still_suppresses_debug(self):
        # The core regression: invalid level must NOT log debug (max verbosity).
        lg = _logger("warning")
        suppressed = lg._levels.get("debug", 0) < lg._levels.get(lg.level, 0)
        self.assertTrue(suppressed)

    def test_valid_warn_suppresses_debug_and_info(self):
        lg = _logger("warn")
        self.assertLess(lg._levels["debug"], lg._levels[lg.level])
        self.assertLess(lg._levels["info"], lg._levels[lg.level])


class TestGetLoggerEnv(unittest.TestCase):
    def test_bad_env_level_does_not_break(self):
        import aictl.core.logging as L
        L._loggers.clear()
        with patch.dict(os.environ, {"AIOS_LOG_LEVEL": "warning"}):
            lg = L.get_logger("env_test")
        self.assertEqual(lg.level, "info")   # fell back, not threshold-0
        L._loggers.clear()


if __name__ == "__main__":
    unittest.main()
