"""Pass 163 (過不足 gap): `doctor --fix` was blind to its own `--deep` findings.

ソクラテス式問答法 for 過不足 (excess/deficiency) surfaced a real 不足: Pass 162
made `aictl doctor --deep` REPORT trust-baseline drift, but `build_remediations`
(the function `--fix` calls) only ever inspected the shallow hardware `report` —
it never looked at trust-baseline state at all, deep mode or not. An operator
running `doctor --fix` after drift was reported would see zero mention of it in
the remediation plan; `--deep` and `--fix` were two disconnected code paths.

Fix: `build_remediations` now also calls `_trust_baseline_remediations(store)`,
which audits every baselined file (via `BaselineStore.check_all()` — no --deep
gate needed, it's a cheap local hash) and emits one remediation entry per
drifted/missing file (capped at 5, folded into a "...and N more" summary beyond
that so a large baseline set doesn't spam the plan).

Critical invariant: every trust-drift remediation entry has `auto: False`.
Silently re-baselining a changed file would be exactly the "just trust the new
hash" mistake that defeats drift detection in the first place — `--fix` must
never do that automatically, only point at manual investigation.
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


class TestTrustBaselineRemediations(unittest.TestCase):
    def _fixes(self, state_dir):
        from aictl.cmd.doctor import _trust_baseline_remediations
        from aictl.core.state import StateStore
        return _trust_baseline_remediations(StateStore(state_dir))

    def test_no_drift_no_remediations(self):
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        _mk(m, "model.gguf", b"data")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        self.assertEqual(self._fixes(state), [])

    def test_no_baselines_no_remediations(self):
        state = Path(tempfile.mkdtemp())
        self.assertEqual(self._fixes(state), [])

    def test_changed_file_produces_remediation(self):
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        f = _mk(m, "model.gguf", b"original")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        f.write_bytes(b"tampered")
        fixes = self._fixes(state)
        self.assertEqual(len(fixes), 1)
        self.assertIn("changed", fixes[0]["issue"])
        self.assertIn(str(f.resolve()), fixes[0]["issue"])

    def test_missing_file_produces_remediation(self):
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        f = _mk(m, "model.gguf", b"data")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        f.unlink()
        fixes = self._fixes(state)
        self.assertIn("missing", fixes[0]["issue"])

    def test_drift_remediation_never_auto(self):
        # The critical safety invariant: never auto-remediate a security drift.
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        f = _mk(m, "model.gguf", b"original")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        f.write_bytes(b"tampered")
        fixes = self._fixes(state)
        self.assertTrue(all(fx["auto"] is False for fx in fixes))

    def test_remediation_suggests_investigation_and_rebaseline(self):
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        f = _mk(m, "model.gguf", b"original")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        f.write_bytes(b"tampered")
        fixes = self._fixes(state)
        self.assertIn("trust check", fixes[0]["command"])
        self.assertIn("trust baseline", fixes[0]["command"])

    def test_many_drifted_files_capped_with_summary(self):
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        for i in range(8):
            _mk(m, f"m{i}.gguf", b"original")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        for i in range(8):
            (m / f"m{i}.gguf").write_bytes(b"tampered")
        fixes = self._fixes(state)
        self.assertEqual(len(fixes), 6)   # 5 individual + 1 summary
        self.assertIn("3 more", fixes[-1]["issue"])


class TestDoctorFixSurfacesTrustDrift(unittest.TestCase):
    def _run_fix(self, state_dir, as_json=False):
        from aictl.cmd.doctor import run, register
        import argparse as _ap
        p = _ap.ArgumentParser(prog="aictl")
        sub = p.add_subparsers()
        register(sub)
        args = argparse.Namespace(state_dir=state_dir, deep=False, fix=True, json=as_json)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run(args)
        return out.getvalue()

    def test_fix_without_deep_flag_still_surfaces_drift(self):
        # The whole point: trust drift must surface from plain `--fix`, not
        # only when `--deep` is also passed — checking baselines is cheap.
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        f = _mk(m, "model.gguf", b"original")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        f.write_bytes(b"tampered")
        text = self._run_fix(str(state))
        self.assertIn("Trust baseline changed", text)

    def test_fix_json_includes_trust_remediation(self):
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        f = _mk(m, "model.gguf", b"original")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        f.write_bytes(b"tampered")
        text = self._run_fix(str(state), as_json=True)
        data = json.loads(text)
        trust_entries = [r for r in data["remediations"] if "Trust baseline" in r["issue"]]
        self.assertEqual(len(trust_entries), 1)
        self.assertFalse(trust_entries[0]["auto"])
        self.assertNotIn(trust_entries[0]["issue"], data["applied"])

    def test_fix_no_drift_no_trust_entries(self):
        state = Path(tempfile.mkdtemp())
        m = Path(tempfile.mkdtemp())
        _mk(m, "model.gguf", b"data")
        from aictl.trust.baseline import BaselineStore
        BaselineStore(state).record(m)
        text = self._run_fix(str(state))
        self.assertNotIn("Trust baseline", text)


if __name__ == "__main__":
    unittest.main()
