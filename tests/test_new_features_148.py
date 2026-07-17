"""Pass 148 (new viewpoint): `aictl trust` — model integrity baseline / drift.

Socratic step: `verify_digest` (Pass 147) checks a model against a digest you were
*given*. The complementary question, unanswered until now, is the one operators
face *after* trusting a model: have my local model bytes changed since
(tampering / bit-rot / a bad sync)? `aictl trust baseline` records a SHA-256
baseline on first use; `aictl trust check` re-hashes and reports per-file drift
(ok / changed / missing / new) with a non-zero exit on drift so CI can gate.

Covers the core BaselineStore (record/check/worst_status, file & directory) and
the command's exit codes.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


def _mk_model(dir_path: Path, name: str, content: bytes) -> Path:
    p = dir_path / name
    p.write_bytes(content)
    return p


class TestBaselineStore(unittest.TestCase):
    def setUp(self):
        from aictl.trust.baseline import BaselineStore
        self.state = Path(tempfile.mkdtemp())
        self.models = Path(tempfile.mkdtemp())
        self.store = BaselineStore(self.state)

    def test_record_then_check_ok(self):
        _mk_model(self.models, "m.safetensors", b"weights-A")
        self.store.record(self.models)
        results = self.store.check(self.models)
        self.assertEqual([r["status"] for r in results], ["ok"])

    def test_changed_file_is_drift(self):
        f = _mk_model(self.models, "m.safetensors", b"weights-A")
        self.store.record(self.models)
        f.write_bytes(b"weights-TAMPERED")
        results = self.store.check(self.models)
        self.assertEqual(results[0]["status"], "changed")
        self.assertNotEqual(results[0]["expected"], results[0]["actual"])

    def test_missing_file_detected(self):
        f = _mk_model(self.models, "m.gguf", b"weights-A")
        self.store.record(self.models)
        f.unlink()
        results = self.store.check(self.models)
        self.assertEqual([r["status"] for r in results], ["missing"])

    def test_new_unbaselined_file(self):
        _mk_model(self.models, "a.gguf", b"A")
        self.store.record(self.models)
        _mk_model(self.models, "b.gguf", b"B")
        statuses = {r["path"].split("/")[-1]: r["status"]
                    for r in self.store.check(self.models)}
        self.assertEqual(statuses["a.gguf"], "ok")
        self.assertEqual(statuses["b.gguf"], "new")

    def test_only_weight_extensions_baselined(self):
        _mk_model(self.models, "model.safetensors", b"W")
        _mk_model(self.models, "config.json", b"{}")        # ignored
        _mk_model(self.models, "tokenizer.txt", b"hi")      # ignored
        recorded = self.store.record(self.models)
        names = {Path(r["path"]).name for r in recorded}
        self.assertEqual(names, {"model.safetensors"})

    def test_single_file_baseline(self):
        f = _mk_model(self.models, "solo.bin", b"solo")
        recorded = self.store.record(f)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(self.store.check(f)[0]["status"], "ok")

    def test_worst_status_severity(self):
        from aictl.trust.baseline import worst_status
        self.assertEqual(worst_status([{"status": "ok"}, {"status": "changed"}]),
                         "changed")
        self.assertEqual(worst_status([{"status": "ok"}, {"status": "new"}]), "new")
        self.assertEqual(worst_status([{"status": "ok"}]), "ok")
        self.assertEqual(
            worst_status([{"status": "new"}, {"status": "missing"}]), "missing")


class TestTrustCommand(unittest.TestCase):
    def _run(self, argv):
        # Mirror the real CLI where --state-dir is a global flag on the main
        # parser (placed before the subcommand).
        from aictl.cmd import trust
        p = argparse.ArgumentParser(prog="aictl")
        p.add_argument("--state-dir", default=None)
        sub = p.add_subparsers()
        trust.register(sub)
        ns = p.parse_args(argv)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with contextlib.redirect_stderr(io.StringIO()):
                code = ns.func(ns)
        return code, buf.getvalue()

    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.models = Path(tempfile.mkdtemp())

    def _sub(self, *subargv):
        return self._run(["--state-dir", str(self.state), *subargv])

    def test_check_ok_exit_0(self):
        _mk_model(self.models, "m.safetensors", b"W")
        self._sub("trust", "baseline", str(self.models))
        code, _ = self._sub("trust", "check", str(self.models))
        self.assertEqual(code, 0)

    def test_check_drift_exit_2(self):
        f = _mk_model(self.models, "m.safetensors", b"W")
        self._sub("trust", "baseline", str(self.models))
        f.write_bytes(b"TAMPERED")
        code, _ = self._sub("trust", "check", str(self.models), "--json")
        self.assertEqual(code, 2)

    def test_baseline_empty_path_exit_1(self):
        empty = Path(tempfile.mkdtemp())
        code, _ = self._sub("trust", "baseline", str(empty))
        self.assertEqual(code, 1)

    def test_list_json(self):
        _mk_model(self.models, "m.gguf", b"W")
        self._sub("trust", "baseline", str(self.models))
        code, out = self._sub("trust", "list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 1)


if __name__ == "__main__":
    unittest.main()
