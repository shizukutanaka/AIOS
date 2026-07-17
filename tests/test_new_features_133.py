"""Pass 133: watch/dash refresh interval must be clamped (no sleep crash).

Research-informed (調査: Qiita/Zenn — `time.sleep(negative)` raises
"ValueError: sleep length must be non-negative"; 0 busy-loops at 100% CPU).

`top`/`health`/`status` already clamp their `--interval` with `max(1, ...)`, but
`watch` and `dash` fed `args.interval` straight to `time.sleep`. So
`watch --interval -5` / `dash --watch --interval -2` crashed the monitor with a
ValueError surfaced as a bogus "report a bug", and `--interval 0` busy-looped.

Both now clamp before the loop (watch: int floor 1; dash: float floor 0.5 to
preserve sub-second refresh), matching the established convention.
"""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch


class _Break(Exception):
    pass


class TestWatchIntervalClamp(unittest.TestCase):
    def _slept_value(self, interval):
        captured = []

        def fake_sleep(n):
            captured.append(n)
            raise KeyboardInterrupt   # exit the watch loop after first tick

        import aictl.cmd.watch as w
        with patch("aictl.cmd.watch.time.sleep", fake_sleep), \
                patch("aictl.cmd.watch._render", lambda *a, **k: None), \
                patch("aictl.cmd.watch.StateStore"), \
                patch("aictl.cmd.watch.load_config"):
            w.run(argparse.Namespace(interval=interval, state_dir=None))
        return captured[0]

    def test_negative_clamped_to_one(self):
        self.assertEqual(self._slept_value(-5), 1)   # was: ValueError

    def test_zero_clamped_to_one(self):
        self.assertEqual(self._slept_value(0), 1)    # was: busy loop

    def test_valid_preserved(self):
        self.assertEqual(self._slept_value(10), 10)


class TestDashIntervalClamp(unittest.TestCase):
    def _slept_value(self, interval):
        captured = []

        def fake_sleep(n):
            captured.append(n)
            raise KeyboardInterrupt

        import aictl.cmd.dash as d
        with patch("aictl.cmd.dash.time.sleep", fake_sleep), \
                patch("aictl.cmd.dash._render", lambda *a, **k: None), \
                patch("aictl.cmd.dash._clear", lambda *a, **k: None):
            d.run(argparse.Namespace(watch=True, interval=interval))
        return captured[0]

    def test_negative_clamped(self):
        self.assertEqual(self._slept_value(-2.0), 0.5)   # was: ValueError

    def test_zero_clamped(self):
        self.assertEqual(self._slept_value(0.0), 0.5)

    def test_subsecond_preserved(self):
        self.assertEqual(self._slept_value(0.5), 0.5)

    def test_valid_preserved(self):
        self.assertEqual(self._slept_value(5.0), 5.0)


if __name__ == "__main__":
    unittest.main()
