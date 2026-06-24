"""Pass 153: eval must reject malformed suite files cleanly (not "report a bug").

長所短所改善点 audit of the config-integrity class: `eval run/compare/report`
loaded user-authored suite/baseline JSON with a bare
`json.loads(path.read_text())` and then called `suite.get("cases", [])`. Two
unhandled failure modes:

  - a malformed JSON file raised an uncaught JSONDecodeError;
  - a non-object root (e.g. a JSON list `[1,2,3]`) reached `.get(...)` and raised
    AttributeError — surfaced to the user as "report a bug" for what is really a
    bad input *file*, not a tool defect (a V4 violation).

Fix: a shared `_load_suite_file` that catches parse/IO errors and validates the
root is a dict, returning a clean (None, message). All four load sites use it.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


def _write(content: str) -> Path:
    d = Path(tempfile.mkdtemp())
    f = d / "suite.json"
    f.write_text(content)
    return f


def _run_eval(suite_path, **extra):
    from aictl.cmd import eval as eval_cmd
    args = argparse.Namespace(suite=str(suite_path), model="auto", json=False, **extra)
    out, errbuf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out):
        with contextlib.redirect_stderr(errbuf):
            code = eval_cmd.run_eval(args)
    return code, out.getvalue() + errbuf.getvalue()


class TestLoadSuiteFile(unittest.TestCase):
    def test_valid_object_loads(self):
        from aictl.cmd.eval import _load_suite_file
        f = _write('{"name": "t", "cases": []}')
        data, errmsg = _load_suite_file(f)
        self.assertEqual(errmsg, "")
        self.assertEqual(data["name"], "t")

    def test_malformed_json_returns_error(self):
        from aictl.cmd.eval import _load_suite_file
        data, errmsg = _load_suite_file(_write("{not json"))
        self.assertIsNone(data)
        self.assertIn("not valid JSON", errmsg)

    def test_list_root_rejected(self):
        from aictl.cmd.eval import _load_suite_file
        data, errmsg = _load_suite_file(_write("[1, 2, 3]"))
        self.assertIsNone(data)
        self.assertIn("must be a JSON object", errmsg)

    def test_scalar_root_rejected(self):
        from aictl.cmd.eval import _load_suite_file
        data, errmsg = _load_suite_file(_write("42"))
        self.assertIsNone(data)
        self.assertIn("must be a JSON object", errmsg)


class TestEvalRunMalformedSuite(unittest.TestCase):
    def test_malformed_json_exit_1_no_crash(self):
        code, _ = _run_eval(_write("{not json"))
        self.assertEqual(code, 1)

    def test_list_root_exit_1_not_bug_report(self):
        code, text = _run_eval(_write("[1,2,3]"))
        self.assertEqual(code, 1)
        self.assertNotIn("report", text.lower())   # never a "report a bug"

    def test_valid_suite_still_runs(self):
        suite = json.dumps({
            "name": "t",
            "cases": [{"id": "c1", "prompt": "hi",
                       "assertions": [{"type": "contains", "value": "x"}]}],
        })
        code, text = _run_eval(_write(suite))
        self.assertIn(code, (0, 1))   # runs to completion (pass/fail), no crash
        self.assertIn("Running", text)


if __name__ == "__main__":
    unittest.main()
