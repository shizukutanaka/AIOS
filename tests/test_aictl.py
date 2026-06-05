"""Test suite for aictl — M0–M5 coverage."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure aictl is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.core.state import StateStore, NodeState, StackEntry
from aictl.core.output import print_table, print_kv
from aictl.stack.manifest import parse_file, StackParseError, get_recipe, list_recipes, _build_manifest
from aictl.trust.verify import sha256_file, verify_digest, TrustPolicy
from aictl.metrics.slo import (
    InferenceMetrics, SystemPressure, SLOTarget, SLOVerdict, check_slo
)
from aictl.runtime.broker import (
    GPUInfo, select_profile, _infer_arch, SystemInfo, RuntimeReport
)


class TestStateStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = StateStore(Path(self.tmp))

    def test_init_state(self):
        self.assertFalse(self.store.is_initialized())
        ns = NodeState(node_id="abc123", hostname="test", profile="cpu-only")
        self.store.save_node(ns)
        self.assertTrue(self.store.is_initialized())

    def test_load_node(self):
        ns = NodeState(node_id="x", hostname="h", profile="nvidia-ada-24gb",
                       gpu_count=1, vram_total_mb=24576, ram_total_mb=65536)
        self.store.save_node(ns)
        loaded = self.store.load_node()
        self.assertEqual(loaded.node_id, "x")
        self.assertEqual(loaded.profile, "nvidia-ada-24gb")
        self.assertEqual(loaded.gpu_count, 1)

    def test_stacks_crud(self):
        self.assertEqual(self.store.load_stacks(), [])
        e = StackEntry(name="test", file="test.json", status="running")
        self.store.upsert_stack(e)
        stacks = self.store.load_stacks()
        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0].name, "test")

        # Update
        e.status = "stopped"
        self.store.upsert_stack(e)
        stacks = self.store.load_stacks()
        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0].status, "stopped")

        # Remove
        self.assertTrue(self.store.remove_stack("test"))
        self.assertFalse(self.store.remove_stack("nonexistent"))
        self.assertEqual(len(self.store.load_stacks()), 0)

    def test_model_registry(self):
        self.store.register_model("m1", "llama3", "sha256:abc", 1000, "gguf")
        models = self.store.list_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "llama3")

    def test_model_upsert(self):
        self.store.register_model("m1", "llama3", "sha256:abc")
        self.store.register_model("m1", "llama3-updated", "sha256:def")
        models = self.store.list_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "llama3-updated")


class TestStackManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_parse_json(self):
        p = Path(self.tmp) / "stack.json"
        p.write_text(json.dumps({
            "name": "test-stack",
            "services": [
                {"name": "llm", "runtime": "ollama", "model": "llama3.2:3b", "port": 11434}
            ],
        }))
        m = parse_file(str(p))
        self.assertEqual(m.name, "test-stack")
        self.assertEqual(len(m.services), 1)
        self.assertEqual(m.services[0].runtime, "ollama")

    def test_parse_missing_name(self):
        p = Path(self.tmp) / "bad.json"
        p.write_text(json.dumps({"services": []}))
        with self.assertRaises(StackParseError):
            parse_file(str(p))

    def test_parse_missing_file(self):
        with self.assertRaises(StackParseError):
            parse_file("/nonexistent/file.json")

    def test_recipes(self):
        names = list_recipes()
        self.assertIn("local-chat", names)
        self.assertIn("team-rag", names)
        self.assertIn("image-gen", names)

    def test_get_recipe(self):
        m = get_recipe("local-chat")
        self.assertIsNotNone(m)
        self.assertEqual(m.name, "local-chat")
        self.assertGreater(len(m.services), 0)

    def test_get_unknown_recipe(self):
        self.assertIsNone(get_recipe("nonexistent"))

    def test_build_manifest_models(self):
        data = {
            "name": "test",
            "models": [{"name": "llama3", "format": "safetensors", "signed": True}],
        }
        m = _build_manifest(data, "test")
        self.assertEqual(len(m.models), 1)
        self.assertTrue(m.models[0].signed)

    def test_trust_policy_field(self):
        data = {"name": "test", "trust_policy": "enforce"}
        m = _build_manifest(data, "test")
        self.assertEqual(m.trust_policy, "enforce")


class TestTrustVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_sha256_file(self):
        p = Path(self.tmp) / "test.bin"
        p.write_bytes(b"hello world")
        digest = sha256_file(p)
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(len(digest), 71)  # sha256: + 64 hex chars

    def test_verify_digest_match(self):
        p = Path(self.tmp) / "test.bin"
        p.write_bytes(b"test data")
        digest = sha256_file(p)
        self.assertTrue(verify_digest(p, digest))

    def test_verify_digest_mismatch(self):
        p = Path(self.tmp) / "test.bin"
        p.write_bytes(b"test data")
        self.assertFalse(verify_digest(p, "sha256:0000"))

    def test_trust_policy_enforce(self):
        tp = TrustPolicy("enforce")
        ok, msg = tp.check("/dev/null", "")
        self.assertFalse(ok)
        self.assertIn("reject", msg.lower())

    def test_trust_policy_warn(self):
        tp = TrustPolicy("warn")
        ok, msg = tp.check("/dev/null", "")
        self.assertTrue(ok)
        self.assertIn("WARNING", msg)

    def test_trust_policy_disabled(self):
        tp = TrustPolicy("disabled")
        ok, msg = tp.check("/dev/null", "sha256:wrong")
        self.assertTrue(ok)


class TestMetricsSLO(unittest.TestCase):
    def test_compliant(self):
        m = InferenceMetrics(ttft_ms_p95=200, itl_ms_p95=30, tokens_per_sec=50,
                             active_requests=5, error_rate=0.01, queue_depth=10,
                             kv_cache_utilization=0.5)
        p = SystemPressure(memory_some_avg10=5.0)
        v = check_slo(m, p, SLOTarget())
        self.assertTrue(v.compliant)
        self.assertEqual(v.action, "none")

    def test_ttft_violation(self):
        m = InferenceMetrics(ttft_ms_p95=800, active_requests=1)
        v = check_slo(m, SystemPressure(), SLOTarget())
        self.assertFalse(v.compliant)
        self.assertTrue(any("TTFT" in x for x in v.violations))

    def test_error_rate_triggers_failover(self):
        m = InferenceMetrics(error_rate=0.15, active_requests=1)
        v = check_slo(m, SystemPressure(), SLOTarget())
        self.assertFalse(v.compliant)
        self.assertEqual(v.action, "failover")

    def test_psi_violation(self):
        m = InferenceMetrics()
        p = SystemPressure(memory_some_avg10=50.0)
        v = check_slo(m, p, SLOTarget())
        self.assertFalse(v.compliant)

    def test_multiple_violations_drain(self):
        m = InferenceMetrics(ttft_ms_p95=800, itl_ms_p95=100,
                             tokens_per_sec=5, active_requests=1,
                             queue_depth=200)
        v = check_slo(m, SystemPressure(), SLOTarget())
        self.assertFalse(v.compliant)
        self.assertGreaterEqual(len(v.violations), 3)


class TestRuntimeBroker(unittest.TestCase):
    def test_select_profile_no_hardware(self):
        self.assertEqual(select_profile([], []), "cpu-only")

    def test_select_profile_nvidia(self):
        gpus = [GPUInfo(0, "RTX 4090", "nvidia", 24576, "535", "8.9")]
        p = select_profile(gpus, [])
        self.assertIn("nvidia", p)
        self.assertIn("ada", p)
        self.assertIn("24gb", p)

    def test_select_profile_amd(self):
        gpus = [GPUInfo(0, "Radeon RX 7900 XTX", "amd", 24576, "", "")]
        p = select_profile(gpus, [])
        self.assertIn("amd", p)
        self.assertIn("rdna3", p)

    def test_select_profile_prefers_more_vram(self):
        gpus = [
            GPUInfo(0, "RTX 3060", "nvidia", 12288, "535", "8.6"),
            GPUInfo(1, "RTX 4090", "nvidia", 24576, "535", "8.9"),
        ]
        p = select_profile(gpus, [])
        self.assertIn("24gb", p)

    def test_infer_arch_hopper(self):
        g = GPUInfo(0, "H100", "nvidia", 80000, "", "")
        self.assertEqual(_infer_arch(g), "hopper")

    def test_infer_arch_ampere(self):
        g = GPUInfo(0, "A100", "nvidia", 80000, "", "")
        self.assertEqual(_infer_arch(g), "ampere")

    def test_infer_arch_cdna(self):
        g = GPUInfo(0, "MI300X", "amd", 192000, "", "")
        self.assertEqual(_infer_arch(g), "cdna")


class TestCLIIntegration(unittest.TestCase):
    """Test CLI argument parsing and basic command execution."""

    def test_main_no_args(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args([])
        self.assertIsNone(args.command)

    def test_init_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["init", "--force"])
        self.assertEqual(args.command, "init")
        self.assertTrue(args.force)

    def test_doctor_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["doctor"])
        self.assertEqual(args.command, "doctor")

    def test_apply_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["apply", "-f", "stack.json"])
        self.assertEqual(args.command, "apply")
        self.assertEqual(args.file, "stack.json")

    def test_recipe_list_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["recipe", "list"])
        self.assertEqual(args.command, "recipe")
        self.assertEqual(args.recipe_cmd, "list")

    def test_upgrade_plan_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["upgrade", "plan", "--target-version", "1.0.0"])
        self.assertEqual(args.target_version, "1.0.0")


if __name__ == "__main__":
    unittest.main()
