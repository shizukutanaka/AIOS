"""Regression tests for the 2nd category audit (CLI / SDK / stack & state)."""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── SDK ────────────────────────────────────────────────────────────
class TestSdkIsLocal(unittest.TestCase):
    def test_loopback_and_lan_are_local(self):
        from aictl.sdk import _is_local_endpoint
        for ep in ("http://localhost:11434", "http://127.0.0.1:8000",
                   "http://192.168.1.50:8000", "http://10.0.0.5:8000",
                   "http://172.16.3.4:8000", "http://gpu-box.local:8000"):
            self.assertTrue(_is_local_endpoint(ep), ep)

    def test_public_host_is_not_local(self):
        from aictl.sdk import _is_local_endpoint
        for ep in ("https://api.openai.com", "http://172.32.0.1:8000",
                   "http://8.8.8.8:8000"):
            self.assertFalse(_is_local_endpoint(ep), ep)


class TestSdkCostSplit(unittest.TestCase):
    def test_total_tokens_split_not_double_counted(self):
        import aictl.sdk as sdk
        captured = {}

        class _CC:
            cost_usd = 0.0
            cost_jpy = 0.0

        def fake_compute(model, input_tokens, output_tokens, is_local):
            captured["in"] = input_tokens
            captured["out"] = output_tokens
            return _CC()

        with mock.patch("aictl.core.cost_per_call.compute", fake_compute):
            # prompt ~= 40 chars -> ~10 input tokens; total=150
            sdk._compute_call_cost("gpt-x", "x" * 40, 150, "https://api.openai.com")
        self.assertEqual(captured["in"], 10)
        self.assertEqual(captured["out"], 140)  # 150 total - 10 input, not 150


# ── CLI: --json clobber fixed ──────────────────────────────────────
class TestCliJsonNotClobbered(unittest.TestCase):
    def test_global_json_survives_subcommands(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        cases = [
            ["--json", "spec", "recommend", "qwen3:7b"],
            ["--json", "route", "ask", "hello"],
            ["--json", "troubleshoot"],
            ["--json", "diff", "a", "b"],
            ["--json", "prompt", "list"],
        ]
        for argv in cases:
            ns = p.parse_args(argv)
            self.assertTrue(getattr(ns, "json", False),
                            f"global --json clobbered for: {argv}")


class TestCliConfigSetInvalid(unittest.TestCase):
    def test_invalid_int_returns_1(self):
        from aictl.cmd import config as config_cmd
        with tempfile.TemporaryDirectory() as td:
            args = argparse.Namespace(key="daemon.port", value="not-an-int",
                                      state_dir=Path(td))
            with redirect_stdout(io.StringIO()):
                rc = config_cmd.run_set(args) if hasattr(config_cmd, "run_set") \
                    else _run_config_set(config_cmd, args)
            self.assertEqual(rc, 1)


def _run_config_set(mod, args):
    # config.py exposes the setter via its registered func; find it generically.
    for name in dir(mod):
        fn = getattr(mod, name)
        if callable(fn) and name.startswith("run") and "set" in name:
            return fn(args)
    raise AssertionError("no config set handler found")


class TestCliMigNonNumeric(unittest.TestCase):
    def test_non_numeric_vram_does_not_crash(self):
        from aictl.cmd import mig
        args = argparse.Namespace(models=["llama3:abc"], json=True)
        with redirect_stdout(io.StringIO()) as buf:
            rc = mig.run_plan(args)
        # Either succeeds with a plan or returns a clean code — must not raise.
        self.assertIn(rc, (0, 1, 2))


class TestCliLoraListJson(unittest.TestCase):
    def test_list_json(self):
        from aictl.cmd import lora
        with tempfile.TemporaryDirectory() as td:
            args = argparse.Namespace(base="", json=True, state_dir=Path(td))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = lora.run_list(args)
            self.assertEqual(rc, 0)
            json.loads(buf.getvalue())  # valid JSON, even if empty list


# ── Stack & state ──────────────────────────────────────────────────
class TestModelServiceHpa(unittest.TestCase):
    def test_max_replicas_never_below_min(self):
        from aictl.stack.modelservice import ModelServiceConfig, generate_helm_values
        for replicas in (0, 1, 2, 5):
            cfg = ModelServiceConfig(model="llama3:8b", replicas=replicas)
            vals = generate_helm_values(cfg)
            auto = vals["autoscaling"]
            self.assertGreaterEqual(auto["maxReplicas"], auto["minReplicas"],
                                    f"replicas={replicas}")


class TestRecommendCpuUnknownRam(unittest.TestCase):
    def test_cpu_profile_unknown_ram_not_empty(self):
        from aictl.runtime.recommend import recommend_for_profile
        recs = recommend_for_profile("cpu-only")  # ram_mb defaults to 0
        self.assertTrue(recs, "cpu-only with unknown RAM should still recommend")


class TestContinuityGc(unittest.TestCase):
    def test_gc_removes_json_snapshot_file(self):
        from aictl.runtime.continuity import ContextContinuityEngine, ContextSnapshot
        with tempfile.TemporaryDirectory() as td:
            eng = ContextContinuityEngine(context_dir=Path(td))
            snap = ContextSnapshot(snapshot_id="s1", engine="vllm", model="m",
                                   created_at=0.0, status="saved")  # old -> gc'd
            (Path(td) / "s1.json").write_text(json.dumps({"x": 1}))
            eng._save_index([snap])
            removed = eng.gc(max_age_hours=1)
            self.assertEqual(removed, 1)
            self.assertFalse((Path(td) / "s1.json").exists(),
                             "gc must delete the .json data file")


class TestStateRegisterModelPreserves(unittest.TestCase):
    def test_registered_at_and_status_preserved(self):
        from aictl.core.state import StateStore
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td))
            store.register_model("m1", "model-1", "sha256:abc", size_bytes=999,
                                 registered_at=12345.0, status="quarantined")
            models = {m["id"] if "id" in m else m.get("model_id"): m
                      for m in store.list_models()}
            row = store.list_models()[0]
            self.assertEqual(row["size_bytes"], 999)
            self.assertEqual(row["status"], "quarantined")
            self.assertEqual(row["registered_at"], 12345.0)


if __name__ == "__main__":
    unittest.main()
