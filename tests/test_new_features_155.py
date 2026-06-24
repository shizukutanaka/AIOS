"""Pass 155: cluster/context loaders must validate a dict root (not crash).

Research-informed (調査: Qiita/Zenn — isinstance(data, dict) after json.loads is
the stdlib idiom for safe config loading; the project forbids jsonschema). This
completes the JSON-load class begun in Passes 153/154 by covering the last two
loaders whose non-object roots still reached an unguarded access:

  - cluster recovery-policy: a list/scalar-rooted policy file parsed cleanly,
    then `{**defaults, **policy}` raised "is not a mapping" -> "report a bug".
  - context import: a list root was tolerated by `"snapshot_id" not in data`,
    but a SCALAR root (`42`) made that membership test raise
    "argument of type 'int' is not iterable" -> "report a bug".

Fix: cluster degrades a non-dict policy to the defaults; context rejects a
non-object root with a clean "expected a JSON object" message. (config import
already had this guard; guard/diff already type-check their roots.)
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


class TestClusterRecoveryPolicyRoot(unittest.TestCase):
    def _run(self, policy_content, set_retries=5):
        d = Path(tempfile.mkdtemp())
        (d / "recovery_policy.json").write_text(policy_content)
        from aictl.cmd.cluster import run_recovery_policy
        args = argparse.Namespace(state_dir=str(d), set_retries=set_retries,
                                  set_delay=-1, json=True)
        out, errbuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            with contextlib.redirect_stderr(errbuf):
                code = run_recovery_policy(args)
        return code, out.getvalue() + errbuf.getvalue()

    def test_list_root_degrades_to_defaults(self):
        code, text = self._run("[1, 2, 3]")
        self.assertEqual(code, 0)
        self.assertNotIn("report", text.lower())
        # The update still applied on top of the default policy.
        self.assertIn('"max_retries": 5', text)

    def test_scalar_root_degrades(self):
        code, text = self._run("42")
        self.assertEqual(code, 0)
        self.assertNotIn("report", text.lower())

    def test_valid_policy_preserved(self):
        code, text = self._run(json.dumps({"max_retries": 9, "restart_delay_s": 7}))
        self.assertEqual(code, 0)
        # set_retries=5 overrides the file's 9.
        self.assertIn('"max_retries": 5', text)
        self.assertIn('"restart_delay_s": 7', text)   # untouched field kept


class TestContextImportRoot(unittest.TestCase):
    def _run(self, content):
        d = Path(tempfile.mkdtemp())
        f = d / "snap.json"
        f.write_text(content)
        from aictl.cmd.context import run_import
        args = argparse.Namespace(file=str(f), state_dir=str(d), json=False)
        out, errbuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            with contextlib.redirect_stderr(errbuf):
                code = run_import(args)
        return code, out.getvalue() + errbuf.getvalue()

    def test_scalar_root_clean_error(self):
        code, text = self._run("42")
        self.assertEqual(code, 1)
        self.assertNotIn("report", text.lower())
        self.assertIn("JSON object", text)

    def test_list_root_clean_error(self):
        code, text = self._run("[1, 2]")
        self.assertEqual(code, 1)
        self.assertNotIn("report", text.lower())

    def test_object_missing_fields_still_validated(self):
        code, text = self._run('{"foo": "bar"}')
        self.assertEqual(code, 1)
        self.assertIn("snapshot_id", text)


if __name__ == "__main__":
    unittest.main()
