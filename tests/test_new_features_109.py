"""Pass 109 (loop): temporal honesty in machine-readable output.

The codebase convention for machine-readable timestamps (report.py, export.py)
is unambiguous UTC ISO-8601 with a trailing 'Z' (time.gmtime() + 'Z'). Two
--json producers diverged and emitted *local* time with no timezone designator,
which a remote consumer silently mislabels by the local UTC offset:

  - eval run: the canonical "timestamp" field (also saved to disk via --save)
    used a bare time.strftime("%Y-%m-%dT%H:%M:%S") -> localtime, no 'Z'.
  - scale status: "evaluated_at" emitted a bare "%H:%M:%S" localtime — not even
    a date — so it was both ambiguous and lossy.

Both now emit "...TZ" UTC. These tests assert the Z suffix and that the value
is real UTC (matches time.gmtime within a small window), so a future regression
back to localtime is caught even when the test host's offset is 0.
"""

from __future__ import annotations

import argparse
import io
import json
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock


def _utc_iso_now_window():
    """Two acceptable UTC second-stamps (now and now-1s) to dodge a tick race."""
    return {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
            for t in (time.time(), time.time() - 1)}


class TestEvalTimestampUTC(unittest.TestCase):
    def test_timestamp_is_utc_with_z(self):
        from aictl.cmd.eval import run_eval
        with TemporaryDirectory() as td:
            suite = Path(td) / "s.json"
            suite.write_text(json.dumps({
                "name": "t",
                "cases": [{"id": "c1", "prompt": "hi",
                           "assertions": [{"type": "contains", "value": ""}]}],
            }))
            buf = io.StringIO()
            with redirect_stdout(buf):
                run_eval(argparse.Namespace(suite=str(suite), model="llama3:8b",
                                            save=None, json=True))
            d = json.loads(buf.getvalue())
        ts = d["timestamp"]
        self.assertTrue(ts.endswith("Z"), ts)
        self.assertIn(ts, _utc_iso_now_window())


class TestScaleEvaluatedAtUTC(unittest.TestCase):
    def test_evaluated_at_is_full_utc_with_z(self):
        from aictl.cmd.scale import run_status

        decision = MagicMock()
        decision.action = "none"
        decision.current_replicas = 1
        decision.desired_replicas = 1
        decision.reason = ""
        decision.metrics = {}
        decision.timestamp = time.time()

        health = MagicMock()
        health.engine = "vllm"
        health.endpoint = "http://x"
        health.reachable = True

        with patch("aictl.cmd.scale.discover_engines", return_value=[health]), \
             patch("aictl.cmd.scale.AutoScaler") as AS:
            AS.return_value.evaluate.return_value = decision
            buf = io.StringIO()
            with redirect_stdout(buf):
                run_status(argparse.Namespace(engine="", json=True))
            rows = json.loads(buf.getvalue())

        ev = rows[0]["evaluated_at"]
        self.assertTrue(ev.endswith("Z"), ev)
        # Full date+time, not the old bare "%H:%M:%S".
        self.assertIn("T", ev)
        self.assertIn(ev, _utc_iso_now_window())


if __name__ == "__main__":
    unittest.main()
