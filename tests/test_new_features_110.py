"""Pass 110 (loop): robust suite-vs-results file disambiguation in eval report.

`aictl eval report --suite <file>` accepts either a *suite definition* (to run)
or a *saved results* file (to summarize). The old sniff peeked at
cases[0]["passed"], so a valid results file with an EMPTY `cases` list (a 0-case
run) was misclassified as a suite and re-run via run_eval — incurring real
inference and discarding the saved summary. The sniff now keys on top-level
summary fields (pass_rate/total/failed) that run_eval writes but a suite
definition never carries.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _report(path: str) -> str:
    from aictl.cmd.eval import run_report
    buf = io.StringIO()
    with redirect_stdout(buf):
        run_report(argparse.Namespace(suite=path, json=False))
    return buf.getvalue()


class TestEvalReportSniff(unittest.TestCase):
    def test_empty_results_file_is_reported_not_rerun(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "results.json"
            p.write_text(json.dumps({
                "suite": "t", "model": "llama3:8b",
                "timestamp": "2026-06-17T01:00:00Z",
                "total": 0, "passed": 0, "failed": 0, "pass_rate": 0,
                "cases": [],
            }))
            # run_eval must NOT be invoked for a results file.
            with patch("aictl.cmd.eval.run_eval") as re_run:
                out = _report(str(p))
                re_run.assert_not_called()
        self.assertIn("Eval Report", out)
        self.assertIn("2026-06-17T01:00:00Z", out)  # saved timestamp surfaced

    def test_nonempty_results_file_lists_cases(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "results.json"
            p.write_text(json.dumps({
                "suite": "t", "model": "m", "timestamp": "x",
                "total": 1, "passed": 1, "failed": 0, "pass_rate": 1.0,
                "cases": [{"id": "c1", "passed": True, "assertions": []}],
            }))
            with patch("aictl.cmd.eval.run_eval") as re_run:
                out = _report(str(p))
                re_run.assert_not_called()
        self.assertIn("c1", out)

    def test_suite_definition_is_still_rerun(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "suite.json"
            p.write_text(json.dumps({
                "name": "t", "model": "auto",
                "cases": [{"id": "c1", "prompt": "hi", "assertions": []}],
            }))
            with patch("aictl.cmd.eval.run_eval", return_value=0) as re_run:
                _report(str(p))
                re_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
