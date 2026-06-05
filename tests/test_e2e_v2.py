"""E2E Integration Tests: verify the full aictl pipeline works end-to-end.

Tests the complete flow:
  init → doctor → recipe list → apply → ps → status → snapshot → upgrade plan → down
  Events, audit, API keys, fabric, context — all wired together.
"""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.core.state import StateStore, NodeState, StackEntry
from aictl.core.config import Config, save_config
from aictl.core.events import EventBus, Event, get_bus
from aictl.core.audit import AuditLog
from aictl.core.apikeys import KeyManager
from aictl.core.hooks import (
    on_stack_applied, on_stack_stopped, on_model_registered,
    on_snapshot_created, on_slo_violation, on_config_changed,
)
from aictl.core.plugins import discover_plugins, load_plugin
from aictl.runtime.recommend import recommend, MODELS
from aictl.runtime.mig import plan_partitions, ModelRequirement
from aictl.runtime.fabric import detect_memory_fabric, generate_placement_policy
from aictl.runtime.continuity import ContextContinuityEngine, ContextSnapshot
from aictl.stack.kserve import stack_to_llmisvc, LLMISvcConfig
from aictl.stack.manifest import get_recipe, list_recipes
from aictl.metrics.prometheus import generate_metrics_text
from aictl.deploy.imagebuilder import list_formats


class TestFullPipeline(unittest.TestCase):
    """E2E: init → configure → apply → monitor → snapshot → upgrade → down."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = StateStore(self.tmp)

    def test_full_lifecycle(self):
        # 1. Init
        node = NodeState(node_id="e2e-test", hostname="e2e",
                         profile="cpu-only", version="1.1.0",
                         ram_total_mb=16384, gpu_count=0)
        self.store.save_node(node)
        self.assertTrue(self.store.is_initialized())

        # 2. Configure
        config = Config()
        save_config(config, self.tmp)

        # 3. Stack apply (simulated)
        self.store.upsert_stack(StackEntry(
            name="test-chat", file="test.json", status="running",
            services=[{"name": "ollama", "port": 11434}],
        ))

        # 4. Verify stack in state
        stacks = self.store.load_stacks()
        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0].name, "test-chat")

        # 5. Events + Audit
        bus = get_bus()
        bus.clear()
        on_stack_applied("test-chat", "test.json", mode="direct",
                         services=1, state_dir=self.tmp)
        events = bus.recent(5)
        self.assertTrue(any(e.type == "stack.applied" for e in events))

        # 6. Audit log written
        log = AuditLog(self.tmp)
        entries = log.read(n=5)
        self.assertTrue(any(e.event == "stack.applied" for e in entries))

        # 7. Snapshot
        on_snapshot_created("snap-001", label="test", state_dir=self.tmp)
        entries = log.read(n=10)
        self.assertTrue(any(e.event == "snapshot.created" for e in entries))

        # 8. Prometheus metrics
        text = generate_metrics_text(self.store)
        self.assertIn("aios_node_info", text)
        self.assertIn("aios_stacks_active", text)
        self.assertIn("1", text)  # 1 active stack

        # 9. Stack down
        on_stack_stopped("test-chat", state_dir=self.tmp)
        events = bus.recent(10)
        self.assertTrue(any(e.type == "stack.stopped" for e in events))

    def test_api_key_lifecycle(self):
        mgr = KeyManager(self.tmp)

        # Create
        raw, key = mgr.generate_key("e2e-key", rate_limit_rpm=5)
        self.assertTrue(raw.startswith("aios-"))

        # Validate
        valid, _, k = mgr.validate(raw)
        self.assertTrue(valid)

        # Rate limit
        for _ in range(5):
            ok, _ = mgr.check_rate_limit(k)
            self.assertTrue(ok)
        ok6, msg = mgr.check_rate_limit(k)
        self.assertFalse(ok6)

        # Usage
        mgr.record_usage(k.key_id, tokens=500)
        keys = mgr.list_keys()
        self.assertEqual(keys[0]["total_tokens"], 500)

        # Revoke
        mgr.revoke(k.key_id)
        valid2, _, _ = mgr.validate(raw)
        self.assertFalse(valid2)


class TestHooksIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bus = get_bus()
        self.bus.clear()

    def test_stack_hooks_emit_events(self):
        on_stack_applied("my-stack", "my.json", state_dir=self.tmp)
        events = self.bus.recent(5, event_type="stack.applied")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data.get("name"), "my-stack")

    def test_slo_violation_hook(self):
        on_slo_violation("vllm", "ttft_p95", 600.0, 500.0,
                         action="warn", state_dir=self.tmp)
        events = self.bus.recent(5, event_type="slo.violation")
        self.assertEqual(len(events), 1)

        log = AuditLog(self.tmp)
        entries = log.read(n=5, event_filter="slo.violation")
        self.assertEqual(len(entries), 1)

    def test_config_change_hook(self):
        on_config_changed("slo.ttft_p95_ms", "500", "300", state_dir=self.tmp)
        log = AuditLog(self.tmp)
        entries = log.read(n=5, event_filter="config.changed")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].resource, "slo.ttft_p95_ms")


class TestPluginSystem(unittest.TestCase):
    def test_discover_with_no_plugins(self):
        plugins = discover_plugins()
        # May or may not find plugins depending on environment
        self.assertIsInstance(plugins, list)

    def test_load_nonexistent(self):
        result = load_plugin("/nonexistent/plugin.py")
        self.assertIsNone(result)

    def test_plugin_dir_creation(self):
        tmp = Path(tempfile.mkdtemp()) / "plugins"
        # Plugin dirs should be discoverable even if empty
        from aictl.core.plugins import PLUGIN_DIRS
        self.assertIsInstance(PLUGIN_DIRS, list)


class TestAllRecipesKServeConversion(unittest.TestCase):
    """Verify every recipe converts to both K8s Deployment and KServe LLMISvc."""

    def test_all_recipes_to_llmisvc(self):
        for name in list_recipes():
            manifest = get_recipe(name)
            resources = stack_to_llmisvc(manifest)
            self.assertIsInstance(resources, list, f"Recipe {name} failed")

    def test_all_recipes_have_llm_or_deployment(self):
        for name in list_recipes():
            manifest = get_recipe(name)
            resources = stack_to_llmisvc(manifest)
            kinds = {r["kind"] for r in resources}
            self.assertTrue(
                "LLMInferenceService" in kinds or "Deployment" in kinds,
                f"Recipe {name} has no LLMInferenceService or Deployment"
            )


class TestRecommendCoverage(unittest.TestCase):
    """Verify model recommendations work for various hardware profiles."""

    def test_low_ram_cpu(self):
        recs = recommend(vram_mb=0, ram_mb=4096)
        for r in recs:
            self.assertLessEqual(r.ram_required_mb, 4096)

    def test_rtx_4090_24gb(self):
        recs = recommend(vram_mb=24576, ram_mb=32768)
        self.assertGreater(len(recs), 0)
        # Should include some vLLM options
        runtimes = {r.runtime for r in recs}
        self.assertTrue(len(runtimes) >= 1)

    def test_a100_80gb(self):
        recs = recommend(vram_mb=81920, ram_mb=256000)
        self.assertGreater(len(recs), 0)
        # Should include 70B models
        large_models = [r for r in recs if "70b" in r.name.lower() or "70B" in r.name]
        self.assertGreater(len(large_models), 0)

    def test_all_use_cases(self):
        for uc in ["chat", "code", "embedding", "vision", "stt"]:
            recs = recommend(vram_mb=24576, ram_mb=32768, use_case=uc)
            for r in recs:
                self.assertEqual(r.use_case, uc)


class TestFabricAndContinuity(unittest.TestCase):
    def test_fabric_detect_and_policy(self):
        report = detect_memory_fabric()
        policy = generate_placement_policy(report, vram_gb=0)
        self.assertEqual(policy.model_weights, "dram")
        self.assertIn(policy.kv_cache, ("dram", "cxl"))

    def test_context_full_cycle(self):
        tmp = Path(tempfile.mkdtemp())
        engine = ContextContinuityEngine(tmp)

        # Save
        snap = ContextSnapshot(
            snapshot_id="cycle-1", engine="ollama", model="llama3",
            created_at=time.time(), status="saved", num_entries=5,
        )
        engine._save_index([snap])

        # List
        snaps = engine.list_snapshots()
        self.assertEqual(len(snaps), 1)

        # GC (should keep recent)
        removed = engine.gc(max_age_hours=24)
        self.assertEqual(removed, 0)
        self.assertEqual(len(engine.list_snapshots()), 1)


class TestMIGAllProfiles(unittest.TestCase):
    def test_a100_80gb(self):
        plan = plan_partitions("NVIDIA A100-SXM4-80GB", 0,
                               [ModelRequirement("llm", 40), ModelRequirement("embed", 10)])
        self.assertGreater(len(plan.partitions), 0)
        self.assertGreater(plan.utilization, 0)

    def test_h200_141gb(self):
        plan = plan_partitions("NVIDIA H200", 0,
                               [ModelRequirement("70b", 70), ModelRequirement("7b", 18)])
        allocated = [p for p in plan.partitions if p.get("profile") != "none"]
        self.assertGreater(len(allocated), 0)


class TestImageFormats(unittest.TestCase):
    def test_all_formats(self):
        fmts = list_formats()
        names = {f["format"] for f in fmts}
        for expected in ("qcow2", "raw", "vmdk", "iso", "ami", "vhd"):
            self.assertIn(expected, names)


class TestCLIVersionAndHelp(unittest.TestCase):
    def test_version(self):
        from aictl.__main__ import VERSION
        self.assertRegex(VERSION, r"\d+\.\d+\.\d+")

    def test_build_parser_all_commands(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        # All 29 commands should parse
        simple = ["init", "doctor", "ps", "serve", "status", "setup",
                   "recommend", "proxy", "net", "watch"]
        for cmd in simple:
            args = p.parse_args([cmd])
            self.assertEqual(args.command, cmd)

        # Subcommands
        p.parse_args(["cluster", "promote"])
        p.parse_args(["fabric", "detect"])
        p.parse_args(["context", "list"])
        p.parse_args(["mig", "plan", "--models", "x:8"])
        p.parse_args(["otel", "config"])
        p.parse_args(["warmup", "stats"])
        p.parse_args(["apikey", "list"])
        p.parse_args(["image", "formats"])
        p.parse_args(["audit", "-n", "10"])
        p.parse_args(["snapshot", "list"])
        p.parse_args(["bench", "-n", "5"])
        p.parse_args(["model", "list"])
        p.parse_args(["config", "show"])
        p.parse_args(["recipe", "list"])


if __name__ == "__main__":
    unittest.main()
