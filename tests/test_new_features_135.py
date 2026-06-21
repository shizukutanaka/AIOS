"""Pass 135: StructuredLogger.read_logs(n<=0) must return [] (not 1 row).

Sibling readers in the codebase agree that a limit of 0 or negative means
"no results": `EventBus.recent` and `RagStore.search` both short-circuit with
`if n <= 0: return []`. `read_logs` did not — its only bound was the in-loop
`if len(entries) >= n: return entries`, which fires after the FIRST append for
any n <= 1 (`1 >= 0` and `1 >= -2` are both True). So `read_logs(0)` returned
one entry and `read_logs(-2)` returned one entry — an off-by-one / wrong-count
bug in the same "unguarded non-positive limit" family as Passes 121/124/134.

`aictl log -n 0` surfaced this directly (showed 1 line for a request of 0).
The fix guards `if n <= 0: return []` up front, matching the established
convention, and leaves positive limits and the level filter untouched.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


def _logger_with(n_entries):
    from aictl.core.logging import StructuredLogger
    lg = StructuredLogger("t", log_dir=Path(tempfile.mkdtemp()), level="debug")
    for i in range(n_entries):
        lg.info(f"msg{i}")
    return lg


class TestReadLogsLimit(unittest.TestCase):
    def test_zero_returns_empty(self):
        self.assertEqual(_logger_with(5).read_logs(n=0), [])

    def test_negative_returns_empty(self):
        self.assertEqual(_logger_with(5).read_logs(n=-3), [])

    def test_positive_exact(self):
        self.assertEqual(len(_logger_with(5).read_logs(n=3)), 3)

    def test_positive_capped_at_available(self):
        self.assertEqual(len(_logger_with(2).read_logs(n=10)), 2)

    def test_level_filter_preserved(self):
        lg = _logger_with(0)
        lg.info("an info")
        lg.error("an error")
        only_err = lg.read_logs(n=10, level="error")
        self.assertEqual(len(only_err), 1)
        self.assertEqual(only_err[0]["level"], "error")

    def test_empty_logdir_returns_empty(self):
        self.assertEqual(_logger_with(0).read_logs(n=5), [])


if __name__ == "__main__":
    unittest.main()
