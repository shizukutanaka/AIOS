"""Tests for Phase 10: Fabric Memory, Context Continuity, expanded CLI."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.fabric import (
    detect_memory_fabric, generate_placement_policy,
    generate_damon_config, MemoryTier, FabricReport, PlacementPolicy,
)
from aictl.runtime.continuity import (
    ContextContinuityEngine, ContextSnapshot,
)
from aictl.core.state import StateStore, NodeState


class TestFabricDetection(unittest.TestCase):
    def test_detect_returns_report(self):
        report = detect_memory_fabric()
        self.assertIsInstance(report, FabricReport)
        self.assertGreater(report.total_capacity_gb, 0)

    def test_has_dram_tier(self):
        report = detect_memory_fabric()
        dram = [t for t in report.tiers if t.name == "dram"]
        self.assertGreater(len(dram), 0)
        self.assertGreater(dram[0].capacity_gb, 0)

    def test_numa_nodes(self):
        report = detect_memory_fabric()
        self.assertGreaterEqual(report.numa_nodes, 1)


class TestPlacementPolicy(unittest.TestCase):
    def test_cpu_only_policy(self):
        report = FabricReport(tiers=[
            MemoryTier("dram", 32, 50, 80, 16),
            MemoryTier("nvme", 500, 7, 10000, 200),
        ])
        policy = generate_placement_policy(report, vram_gb=0)
        self.assertEqual(policy.model_weights, "dram")
        self.assertEqual(policy.kv_cache, "dram")
        self.assertEqual(policy.kv_cache_overflow, "nvme")

    def test_gpu_policy(self):
        report = FabricReport(tiers=[
            MemoryTier("dram", 64, 50, 80, 32),
        ])
        policy = generate_placement_policy(report, vram_gb=24)
        self.assertEqual(policy.model_weights, "vram")

    def test_cxl_policy(self):
        report = FabricReport(tiers=[
            MemoryTier("dram", 64, 50, 80, 32),
            MemoryTier("cxl", 128, 32, 200, 128),
            MemoryTier("nvme", 1000, 7, 10000, 500),
        ])
        policy = generate_placement_policy(report, vram_gb=80)
        self.assertEqual(policy.kv_cache_overflow, "cxl")
        self.assertEqual(policy.rag_cache, "cxl")
        self.assertEqual(policy.context_snapshots, "nvme")


class TestDAMONConfig(unittest.TestCase):
    def test_generate_config(self):
        config = generate_damon_config(pid=12345)
        self.assertIn("sysfs_writes", config)
        self.assertIn("notes", config)
        self.assertIn("12345", str(config["sysfs_writes"]))


class TestContextContinuity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.engine = ContextContinuityEngine(self.tmp)

    def test_empty_list(self):
        snaps = self.engine.list_snapshots()
        self.assertEqual(len(snaps), 0)

    def test_save_index(self):
        snaps = [ContextSnapshot(
            snapshot_id="test-1", engine="ollama", model="llama3",
            created_at=time.time(), status="saved", num_entries=3,
        )]
        self.engine._save_index(snaps)
        loaded = self.engine.list_snapshots()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].snapshot_id, "test-1")
        self.assertEqual(loaded[0].engine, "ollama")

    def test_gc_removes_old(self):
        old_snap = ContextSnapshot(
            snapshot_id="old-1", engine="vllm", model="llama3",
            created_at=time.time() - 200000, status="saved",
        )
        self.engine._save_index([old_snap])

        removed = self.engine.gc(max_age_hours=1)
        self.assertEqual(removed, 1)
        self.assertEqual(len(self.engine.list_snapshots()), 0)

    def test_gc_keeps_recent(self):
        recent = ContextSnapshot(
            snapshot_id="recent-1", engine="vllm", model="llama3",
            created_at=time.time(), status="saved",
        )
        self.engine._save_index([recent])
        removed = self.engine.gc(max_age_hours=24)
        self.assertEqual(removed, 0)
        self.assertEqual(len(self.engine.list_snapshots()), 1)


class TestPhase10CLI(unittest.TestCase):
    def test_fabric_detect(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["fabric", "detect"])
        self.assertEqual(args.fabric_cmd, "detect")

    def test_fabric_policy(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["fabric", "policy"])
        self.assertEqual(args.fabric_cmd, "policy")

    def test_context_save(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["context", "save"])
        self.assertEqual(args.context_cmd, "save")

    def test_context_gc(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["context", "gc", "--max-age", "48"])
        self.assertEqual(args.max_age, 48)

    def test_all_29_commands(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        simple = ["init", "doctor", "ps", "serve", "status", "setup",
                   "recommend", "proxy", "net", "watch"]
        for cmd in simple:
            args = p.parse_args([cmd])
            self.assertEqual(args.command, cmd, f"Failed: {cmd}")
        # Subcommand tests
        p.parse_args(["fabric", "detect"])
        p.parse_args(["context", "list"])
        p.parse_args(["mig", "plan", "--models", "x:8"])
        p.parse_args(["cluster", "export", "test"])


if __name__ == "__main__":
    unittest.main()


class TestDaemonNewEndpoints(unittest.TestCase):
    """Test new daemon API endpoints."""

    @classmethod
    def setUpClass(cls):
        import threading
        from aictl.daemon.aiosd import AIOSHandler, ThreadedHTTPServer
        from http.server import HTTPServer

        cls.tmp = tempfile.mkdtemp()
        store = StateStore(Path(cls.tmp))
        store.save_node(NodeState(node_id="ep-test", hostname="test",
                                  profile="cpu-only", vram_total_mb=0, ram_total_mb=16384))
        AIOSHandler.store = store

        cls.port = 17704
        cls.server = ThreadedHTTPServer(("127.0.0.1", cls.port), AIOSHandler)
        cls.server._start_time = time.time()
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        import urllib.request
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())

    def test_fabric_endpoint(self):
        data = self._get("/v1/fabric")
        self.assertIn("fabric", data)
        self.assertIn("placement_policy", data)
        self.assertIn("tiers", data["fabric"])

    def test_context_endpoint(self):
        data = self._get("/v1/context")
        self.assertIn("snapshots", data)

    def test_recommend_endpoint(self):
        data = self._get("/v1/recommend")
        self.assertIn("recommendations", data)
        self.assertIsInstance(data["recommendations"], list)

    def test_apikeys_endpoint(self):
        data = self._get("/v1/apikeys")
        self.assertIn("keys", data)

    def test_audit_endpoint(self):
        data = self._get("/v1/audit")
        self.assertIn("entries", data)

    def test_total_endpoints_count(self):
        """Verify all 19 GET endpoints work (excluding /metrics which is text)."""
        import urllib.request
        endpoints = [
            "/v1/health", "/v1/node", "/v1/runtime", "/v1/stacks",
            "/v1/services", "/v1/models", "/v1/recipes",
            "/v1/metrics/slo", "/v1/metrics/psi", "/v1/upgrade/plan",
            "/v1/broker/engines", "/v1/cluster",
            "/v1/events",
            "/v1/fabric", "/v1/context", "/v1/recommend",
            "/v1/apikeys", "/v1/audit",
        ]
        for ep in endpoints:
            try:
                self._get(ep)
            except Exception as e:
                self.fail(f"Endpoint {ep} failed: {e}")

        # /metrics returns text, not JSON
        url = f"http://127.0.0.1:{self.port}/metrics"
        with urllib.request.urlopen(url, timeout=5) as r:
            body = r.read().decode()
            self.assertIn("aios_node_info", body)
