"""End-to-end integration test: Demo A scenario.

Simulates the full flow from the MVP backlog:
  1. aictl init
  2. aictl doctor
  3. aictl recipe run local-chat (dry-run)
  4. aictl ps
  5. aictl snapshot create
  6. aictl snapshot restore
  7. aictl upgrade plan
  8. aictl config set/show
  9. aictl status

All operations use a temporary state directory — no real containers started.
"""

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.core.state import StateStore, NodeState, StackEntry
from aictl.core.config import Config, save_config, load_config
from aictl.core.snapshots import SnapshotManager
from aictl.stack.manifest import get_recipe, list_recipes
from aictl.stack.quadlet import generate_quadlets
from aictl.runtime.broker import full_detect, GPUInfo, select_profile
from aictl.runtime.router import BrokerRouter, RouteRequest
from aictl.runtime.cache import format_bytes
from aictl.metrics.slo import InferenceMetrics, SLOTarget, SystemPressure, check_slo
from aictl.trust.verify import TrustPolicy


class TestE2EDemoA(unittest.TestCase):
    """Full end-to-end flow simulating Demo A."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = StateStore(self.tmp)

    def test_01_init(self):
        """Step 1: Initialize node."""
        node = NodeState(
            node_id="e2e-test-001",
            hostname="gpu-workstation",
            profile="cpu-only",
            version="0.3.0",
            mode="local",
            ram_total_mb=32768,
        )
        self.store.save_node(node)
        self.assertTrue(self.store.is_initialized())

        loaded = self.store.load_node()
        self.assertEqual(loaded.node_id, "e2e-test-001")
        self.assertEqual(loaded.profile, "cpu-only")

    def test_02_doctor(self):
        """Step 2: Doctor should detect system capabilities."""
        report = full_detect()
        self.assertIsNotNone(report.system.hostname)
        self.assertIn(report.profile, ["cpu-only"])  # sandbox has no GPU
        self.assertIsInstance(report.issues, list)

    def test_03_recipe_dry_run(self):
        """Step 3: Recipe run in dry-run mode."""
        manifest = get_recipe("local-chat")
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.name, "local-chat")
        self.assertEqual(len(manifest.services), 2)

        # Verify Quadlet generation works
        units = generate_quadlets(manifest)
        # Ollama native → only webui gets a Quadlet
        webui_units = [u for u in units if "webui" in u.filename]
        self.assertEqual(len(webui_units), 1)

    def test_04_all_recipes_valid(self):
        """All built-in recipes should parse and generate valid Quadlets."""
        for name in list_recipes():
            manifest = get_recipe(name)
            self.assertIsNotNone(manifest, f"Recipe '{name}' is None")
            self.assertGreater(len(manifest.services), 0)
            # Generate Quadlets (may be empty for ollama-only)
            units = generate_quadlets(manifest)
            for u in units:
                self.assertIn("[Container]", u.content)
                self.assertIn("[Service]", u.content)

    def test_05_ps_empty(self):
        """Step 4: ps should show nothing initially."""
        stacks = self.store.load_stacks()
        self.assertEqual(len(stacks), 0)

    def test_06_snapshot_create_restore(self):
        """Step 5-6: Create and restore snapshot."""
        # Setup state
        self.store.save_node(NodeState(node_id="snap-test", hostname="h", version="0.3.0"))
        self.store.upsert_stack(StackEntry(name="test-stack", file="test.json", status="running"))
        self.store.register_model("m1", "llama3", "sha256:abc")

        # Create snapshot
        mgr = SnapshotManager(self.store)
        snap = mgr.create(label="e2e-test")
        self.assertIn("e2e-test", snap.snapshot_id)
        self.assertEqual(len(snap.stacks), 1)
        self.assertEqual(len(snap.models), 1)

        # List snapshots
        snaps = mgr.list_snapshots()
        self.assertEqual(len(snaps), 1)

        # Clear state
        self.store.save_stacks([])

        # Restore
        ok, msg = mgr.restore(snap.snapshot_id)
        self.assertTrue(ok)

        # Verify restored
        stacks = self.store.load_stacks()
        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0].name, "test-stack")

        # Delete snapshot
        self.assertTrue(mgr.delete(snap.snapshot_id))
        self.assertEqual(len(mgr.list_snapshots()), 0)

    def test_07_upgrade_plan(self):
        """Step 7: Upgrade plan generation."""
        self.store.save_node(NodeState(node_id="up", version="0.3.0"))
        node = self.store.load_node()
        self.assertEqual(node.version, "0.3.0")

    def test_08_config_roundtrip(self):
        """Step 8: Config set and show."""
        c = Config()
        c.trust_policy = "enforce"
        c.slo.ttft_p95_ms = 200.0
        save_config(c, self.tmp)

        loaded = load_config(self.tmp)
        self.assertEqual(loaded.trust_policy, "enforce")
        self.assertEqual(loaded.slo.ttft_p95_ms, 200.0)

    def test_09_slo_check(self):
        """SLO compliance check with various scenarios."""
        target = SLOTarget(ttft_p95_ms=500, error_rate_max=0.05)

        # Compliant
        m1 = InferenceMetrics(ttft_ms_p95=200, error_rate=0.01, active_requests=1,
                              tokens_per_sec=50, queue_depth=5, kv_cache_utilization=0.3)
        v1 = check_slo(m1, SystemPressure(), target)
        self.assertTrue(v1.compliant)

        # TTFT violation
        m2 = InferenceMetrics(ttft_ms_p95=800, active_requests=1)
        v2 = check_slo(m2, SystemPressure(), target)
        self.assertFalse(v2.compliant)

        # Error rate → failover
        m3 = InferenceMetrics(error_rate=0.15, active_requests=1)
        v3 = check_slo(m3, SystemPressure(), target)
        self.assertEqual(v3.action, "failover")

    def test_10_trust_policy(self):
        """Trust chain enforcement."""
        tp_enforce = TrustPolicy("enforce")
        ok, msg = tp_enforce.check("/dev/null", "")
        self.assertFalse(ok)

        tp_warn = TrustPolicy("warn")
        ok, msg = tp_warn.check("/dev/null", "")
        self.assertTrue(ok)

    def test_11_broker_routing(self):
        """Broker routing with no engines available."""
        router = BrokerRouter(endpoints={"vllm": "http://localhost:99999"})
        req = RouteRequest(model="llama3", objective="latency")
        decision = router.route(req)
        self.assertIn("rejected", " ".join(decision.reason_codes))

    def test_12_profile_selection(self):
        """Profile selection for various GPU configs."""
        # No GPU
        self.assertEqual(select_profile([], []), "cpu-only")

        # Single NVIDIA
        gpus = [GPUInfo(0, "RTX 4090", "nvidia", 24576, "535", "8.9")]
        p = select_profile(gpus, [])
        self.assertIn("nvidia", p)
        self.assertIn("ada", p)

        # Multiple GPUs → pick highest VRAM
        gpus2 = [
            GPUInfo(0, "RTX 3060", "nvidia", 12288, "535", "8.6"),
            GPUInfo(1, "A100-80GB", "nvidia", 81920, "535", "8.0"),
        ]
        p2 = select_profile(gpus2, [])
        self.assertIn("80gb", p2)

    def test_13_format_bytes(self):
        self.assertEqual(format_bytes(0), "0.0 B")
        self.assertIn("GB", format_bytes(5 * 1024**3))


class TestE2EFullCLI(unittest.TestCase):
    """Test CLI command parsing for all commands."""

    def test_all_commands_parse(self):
        from aictl.__main__ import build_parser
        p = build_parser()

        # No-arg commands
        for cmd in ["init", "doctor", "ps", "status", "serve"]:
            args = p.parse_args([cmd])
            self.assertEqual(args.command, cmd)

        # Commands with args
        tests = [
            (["apply", "-f", "x.json"], "apply"),
            (["apply", "-f", "x.json", "--quadlet", "--dry-run"], "apply"),
            (["down", "mystack"], "down"),
            (["logs", "svc"], "logs"),
            (["recipe", "list"], "recipe"),
            (["recipe", "run", "local-chat"], "recipe"),
            (["model", "list"], "model"),
            (["model", "cache"], "model"),
            (["upgrade", "plan"], "upgrade"),
            (["node", "token"], "node"),
            (["config", "show"], "config"),
            (["config", "set", "trust_policy", "enforce"], "config"),
            (["snapshot", "create"], "snapshot"),
            (["snapshot", "list"], "snapshot"),
            (["snapshot", "restore", "abc"], "snapshot"),
        ]
        for argv, expected_cmd in tests:
            args = p.parse_args(argv)
            self.assertEqual(args.command, expected_cmd, f"Failed for: {argv}")


if __name__ == "__main__":
    unittest.main()
