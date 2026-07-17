"""Pass 161 (new viewpoint): baseline provenance — connect `model pull` to `trust`.

Socratic step: Pass 148 gave `aictl trust baseline` trust-on-first-use drift
detection, and Pass 160 taught cosign verification to warn when identity isn't
pinned. The next question: a bare `trust baseline` blindly hashes whatever bytes
are on disk *right now* — it can't distinguish "freshly pulled from a registry
this second" from "sitting on disk for six months, possibly already tampered
before anyone got around to baselining it". Nothing connected `model pull`'s own
registry-attested digest into the baseline system at all.

`BaselineStore.record()` gains an optional `source` string (provenance: why this
baseline should be trusted); `model pull --baseline` tags it automatically as
`pull:<reference>`; `trust check`/`trust list` surface it in a new SOURCE column
so an operator can tell a registry-attested baseline apart from an untagged one.

Covers: BaselineStore.record/check source propagation, backward compatibility
with pre-existing (source-less) baseline entries, the `model pull --baseline`
wiring, and the `trust baseline --source` CLI flag.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


def _mk(dir_path: Path, name: str, content: bytes) -> Path:
    p = dir_path / name
    p.write_bytes(content)
    return p


class TestBaselineSourceField(unittest.TestCase):
    def setUp(self):
        from aictl.trust.baseline import BaselineStore
        self.state = Path(tempfile.mkdtemp())
        self.models = Path(tempfile.mkdtemp())
        self.store = BaselineStore(self.state)

    def test_record_with_source_persists_it(self):
        _mk(self.models, "m.safetensors", b"W")
        recorded = self.store.record(self.models, source="pull:ghcr.io/x/y:v1")
        self.assertEqual(recorded[0]["source"], "pull:ghcr.io/x/y:v1")

    def test_record_default_source_is_empty(self):
        _mk(self.models, "m.gguf", b"W")
        recorded = self.store.record(self.models)
        self.assertEqual(recorded[0]["source"], "")

    def test_check_surfaces_source_for_ok_status(self):
        _mk(self.models, "m.safetensors", b"W")
        self.store.record(self.models, source="pull:ghcr.io/x/y:v1")
        results = self.store.check(self.models)
        self.assertEqual(results[0]["source"], "pull:ghcr.io/x/y:v1")

    def test_check_surfaces_source_for_changed_status(self):
        f = _mk(self.models, "m.safetensors", b"original")
        self.store.record(self.models, source="pull:ghcr.io/x/y:v1")
        f.write_bytes(b"tampered")
        results = self.store.check(self.models)
        self.assertEqual(results[0]["status"], "changed")
        self.assertEqual(results[0]["source"], "pull:ghcr.io/x/y:v1")

    def test_pre_existing_source_less_entry_degrades_gracefully(self):
        # Simulate a baseline written before "source" existed (Pass 148 shape).
        import json as _json
        key = str((self.models / "old.gguf").resolve())
        _mk(self.models, "old.gguf", b"legacy")
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "trust_baseline.json").write_text(_json.dumps({
            key: {"digest": self._digest(self.models / "old.gguf"),
                 "size": 6, "recorded_at": 1.0},   # no "source" key at all
        }))
        results = self.store.check(self.models)
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[0]["source"], "")   # backward-compat default

    def _digest(self, path):
        from aictl.trust.verify import sha256_file
        return sha256_file(path)


class TestModelPullBaselineWiring(unittest.TestCase):
    def test_pull_with_baseline_flag_records_provenance(self):
        pulled_dir = Path(tempfile.mkdtemp())
        _mk(pulled_dir, "model.safetensors", b"registry-bytes")
        fake_result = MagicMock(success=True, local_path=str(pulled_dir),
                                digest="sha256:abc", error="")

        from aictl.cmd import model
        state_dir = tempfile.mkdtemp()
        args = argparse.Namespace(reference="ghcr.io/org/llama3-8b:v1", output="",
                                  baseline=True, json=False, state_dir=state_dir)
        buf = io.StringIO()
        with patch("aictl.trust.oras.pull_model", return_value=fake_result):
            with patch("aictl.trust.oras.oras_available", return_value=True):
                with contextlib.redirect_stdout(buf):
                    code = model.run_pull(args)
        self.assertEqual(code, 0)

        from aictl.trust.baseline import BaselineStore
        data = BaselineStore(Path(state_dir)).list_all()
        self.assertEqual(len(data), 1)
        entry = next(iter(data.values()))
        self.assertEqual(entry["source"], "pull:ghcr.io/org/llama3-8b:v1")

    def test_pull_without_baseline_flag_records_nothing(self):
        pulled_dir = Path(tempfile.mkdtemp())
        _mk(pulled_dir, "model.safetensors", b"registry-bytes")
        fake_result = MagicMock(success=True, local_path=str(pulled_dir),
                                digest="sha256:abc", error="")

        from aictl.cmd import model
        state_dir = tempfile.mkdtemp()
        args = argparse.Namespace(reference="ghcr.io/org/llama3-8b:v1", output="",
                                  baseline=False, json=False, state_dir=state_dir)
        buf = io.StringIO()
        with patch("aictl.trust.oras.pull_model", return_value=fake_result):
            with patch("aictl.trust.oras.oras_available", return_value=True):
                with contextlib.redirect_stdout(buf):
                    model.run_pull(args)

        from aictl.trust.baseline import BaselineStore
        self.assertEqual(BaselineStore(Path(state_dir)).list_all(), {})

    def test_failed_pull_never_baselines(self):
        fake_result = MagicMock(success=False, local_path="", digest="", error="404")
        from aictl.cmd import model
        state_dir = tempfile.mkdtemp()
        args = argparse.Namespace(reference="ghcr.io/org/nope:v1", output="",
                                  baseline=True, json=False, state_dir=state_dir)
        buf = io.StringIO()
        with patch("aictl.trust.oras.pull_model", return_value=fake_result):
            with patch("aictl.trust.oras.oras_available", return_value=True):
                with contextlib.redirect_stdout(buf):
                    code = model.run_pull(args)
        self.assertEqual(code, 1)
        from aictl.trust.baseline import BaselineStore
        self.assertEqual(BaselineStore(Path(state_dir)).list_all(), {})


class TestTrustBaselineSourceCliFlag(unittest.TestCase):
    def test_source_flag_parses_and_persists(self):
        from aictl.cmd import trust
        p = argparse.ArgumentParser(prog="aictl")
        p.add_argument("--state-dir", default=None)
        sub = p.add_subparsers()
        trust.register(sub)

        models = Path(tempfile.mkdtemp())
        _mk(models, "m.gguf", b"data")
        state_dir = tempfile.mkdtemp()
        ns = p.parse_args(["--state-dir", state_dir, "trust", "baseline",
                           str(models), "--source", "reviewed-by-secteam", "--json"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ns.func(ns)
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["recorded"][0]["source"], "reviewed-by-secteam")


if __name__ == "__main__":
    unittest.main()
