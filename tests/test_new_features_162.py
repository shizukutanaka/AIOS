"""Pass 162 (next step): trust-baseline drift folded into routine health checks.

次のステップ analysis: Passes 148/160/161 built a real trust chain (verify ->
baseline/drift -> provenance), but it was entirely opt-in and stood apart from
the product's actual daily-use surface — `aictl doctor --deep` (its comprehensive
health check covering security/fabric/network/guardrails/cache/RAG) never once
touched it. An operator would have to remember, separately, to run
`aictl trust check <path>` per model directory, or drift silently goes
unnoticed. The natural next step: make trust-baseline drift part of the routine
health-check surface instead of a separate thing to remember.

Two additions:
  1. `BaselineStore.check_all()` — audits EVERY baselined file system-wide with
     no target path needed (unlike `check(target)`, which requires the caller
     to already know a specific directory). This is what a system-wide health
     check needs: "has anything drifted, anywhere?", not "check this one path".
  2. `aictl trust check` (no path argument) now calls `check_all()`, and
     `aictl doctor --deep` calls it as a new "Trust Baseline" section (both
     human output and --json), following the exact pattern of the existing
     Security/Fabric/RAG sections. Zero baselines recorded is informational
     (○), not a failure — baselining stays fully opt-in.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


def _mk(dir_path: Path, name: str, content: bytes) -> Path:
    p = dir_path / name
    p.write_bytes(content)
    return p


class TestCheckAll(unittest.TestCase):
    def setUp(self):
        from aictl.trust.baseline import BaselineStore
        self.state = Path(tempfile.mkdtemp())
        self.store = BaselineStore(self.state)

    def test_no_baselines_returns_empty(self):
        self.assertEqual(self.store.check_all(), [])

    def test_all_ok_across_separate_directories(self):
        m1, m2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        _mk(m1, "a.gguf", b"aaa")
        _mk(m2, "b.safetensors", b"bbb")
        self.store.record(m1, source="pull:x")
        self.store.record(m2)
        results = self.store.check_all()
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["status"] == "ok" for r in results))

    def test_detects_drift_without_a_target_path(self):
        m1, m2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        f1 = _mk(m1, "a.gguf", b"aaa")
        _mk(m2, "b.safetensors", b"bbb")
        self.store.record(m1, source="pull:x")
        self.store.record(m2)
        f1.write_bytes(b"TAMPERED")
        results = self.store.check_all()
        statuses = {r["path"]: r["status"] for r in results}
        self.assertEqual(statuses[str(f1.resolve())], "changed")

    def test_detects_missing_file(self):
        m = Path(tempfile.mkdtemp())
        f = _mk(m, "a.gguf", b"aaa")
        self.store.record(m)
        f.unlink()
        results = self.store.check_all()
        self.assertEqual(results[0]["status"], "missing")

    def test_no_new_status_possible(self):
        # check_all only re-checks what's already recorded; "new" requires a
        # target directory to scan for un-baselined files, which doesn't apply.
        m = Path(tempfile.mkdtemp())
        _mk(m, "a.gguf", b"aaa")
        self.store.record(m)
        statuses = {r["status"] for r in self.store.check_all()}
        self.assertNotIn("new", statuses)


class TestTrustCheckNoPathIsSystemWide(unittest.TestCase):
    def _cli(self, argv):
        from aictl.cmd import trust
        p = argparse.ArgumentParser(prog="aictl")
        p.add_argument("--state-dir", default=None)
        sub = p.add_subparsers()
        trust.register(sub)
        ns = p.parse_args(argv)
        out, errbuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            with contextlib.redirect_stderr(errbuf):
                code = ns.func(ns)
        return code, out.getvalue() + errbuf.getvalue()

    def test_no_path_audits_everything(self):
        state = tempfile.mkdtemp()
        m1, m2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        _mk(m1, "a.gguf", b"aaa")
        _mk(m2, "b.safetensors", b"bbb")
        self._cli(["--state-dir", state, "trust", "baseline", str(m1)])
        self._cli(["--state-dir", state, "trust", "baseline", str(m2)])
        code, text = self._cli(["--state-dir", state, "trust", "check"])
        self.assertEqual(code, 0)
        self.assertIn("a.gguf", text)
        self.assertIn("b.safetensors", text)

    def test_no_baselines_clean_error(self):
        state = tempfile.mkdtemp()
        code, text = self._cli(["--state-dir", state, "trust", "check"])
        self.assertEqual(code, 1)
        self.assertIn("No baselines recorded", text)

    def test_explicit_path_still_works(self):
        state = tempfile.mkdtemp()
        m = Path(tempfile.mkdtemp())
        _mk(m, "a.gguf", b"aaa")
        self._cli(["--state-dir", state, "trust", "baseline", str(m)])
        code, text = self._cli(["--state-dir", state, "trust", "check", str(m)])
        self.assertEqual(code, 0)


class TestDoctorSurfacesTrustBaseline(unittest.TestCase):
    def _run_deep(self, state_dir, as_json=False):
        from aictl.cmd import doctor
        args = argparse.Namespace(state_dir=state_dir, deep=True, fix=False, json=as_json)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            doctor.run(args)
        return out.getvalue()

    def test_no_baselines_is_informational_not_failure(self):
        state = tempfile.mkdtemp()
        text = self._run_deep(state)
        self.assertIn("Trust Baseline", text)
        self.assertIn("No baselines recorded", text)
        self.assertNotIn("✗ Trust baseline", text)

    def test_healthy_baseline_shows_ok(self):
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        _mk(m, "model.gguf", b"data")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m, source="pull:ghcr.io/x/y:v1")
        text = self._run_deep(str(state))
        self.assertIn("1 baselined file(s), all match", text)

    def test_drift_surfaces_in_human_output(self):
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        f = _mk(m, "model.gguf", b"original")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        f.write_bytes(b"tampered")
        text = self._run_deep(str(state))
        self.assertIn("Integrity drift", text)
        self.assertIn("model.gguf", text)

    def test_drift_surfaces_in_json_output(self):
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        f = _mk(m, "model.gguf", b"original")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        f.write_bytes(b"tampered")
        text = self._run_deep(str(state), as_json=True)
        data = json.loads(text)
        self.assertEqual(data["trust_baseline"]["status"], "changed")

    def test_no_baselines_json_status_ok(self):
        state = tempfile.mkdtemp()
        text = self._run_deep(state, as_json=True)
        data = json.loads(text)
        self.assertEqual(data["trust_baseline"]["status"], "ok")
        self.assertEqual(data["trust_baseline"]["results"], [])


if __name__ == "__main__":
    unittest.main()
