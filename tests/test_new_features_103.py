"""Pass 103 (loop): perf loader filters unknown fields (forward-compat).

Completes the schema-resilience class. read_recent already skipped a fully
malformed line, but raw PerfRecord(**d) meant a record written by a newer aictl
(with an added field) raised TypeError and was dropped entirely. It now filters
unknown keys, so such records still load with their known fields.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestPerfLoaderResilient(unittest.TestCase):

    def _write(self, d, lines):
        from aictl.core import perf
        p = perf._perf_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n")

    def test_unknown_field_record_still_loads(self):
        from aictl.core import perf
        with tempfile.TemporaryDirectory() as d, \
             patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
            self._write(d, [
                json.dumps({"timestamp": 1.0, "command": "new", "duration_ms": 5.0,
                            "exit_code": 0, "rss_mb_peak": 10.0, "added_later": 99}),
                json.dumps({"timestamp": 2.0, "command": "old", "duration_ms": 3.0,
                            "exit_code": 0, "rss_mb_peak": 8.0}),
            ])
            cmds = [r.command for r in perf.read_recent()]
            self.assertIn("new", cmds)   # pre-fix: dropped on TypeError
            self.assertIn("old", cmds)

    def test_garbage_line_skipped_not_fatal(self):
        from aictl.core import perf
        with tempfile.TemporaryDirectory() as d, \
             patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
            self._write(d, [
                "{ not json",
                json.dumps({"timestamp": 1.0, "command": "ok", "duration_ms": 1.0,
                            "exit_code": 0, "rss_mb_peak": 1.0}),
                "[]",  # valid json, wrong shape (not a dict)
            ])
            cmds = [r.command for r in perf.read_recent()]
            self.assertEqual(cmds, ["ok"])


if __name__ == "__main__":
    unittest.main()
