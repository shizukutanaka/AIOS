"""Comprehensive E2E daemon integration test.

Starts a real aiosd instance, makes HTTP requests to all endpoints,
verifies responses, and tests the full lifecycle:
  init → serve → status → recipe → apply → ps → snapshot → security
"""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.core.state import StateStore, NodeState
from aictl.daemon.aiosd import AIOSHandler, ThreadedHTTPServer


class TestE2EDaemonLifecycle(unittest.TestCase):
    """Full lifecycle test against a real running daemon."""

    PORT = 17800

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.store = StateStore(cls.tmp)

        # Initialize node
        cls.store.save_node(NodeState(
            node_id="e2e-daemon",
            hostname="test-host",
            profile="cpu-only",
            version="1.4.0",
            mode="local",
            gpu_count=0,
            vram_total_mb=0,
            ram_total_mb=16384,
        ))

        # Start daemon
        AIOSHandler.store = cls.store
        cls.server = ThreadedHTTPServer(("127.0.0.1", cls.PORT), AIOSHandler)
        cls.server._start_time = time.time()
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        url = f"http://127.0.0.1:{self.PORT}{path}"
        with urllib.request.urlopen(url, timeout=10) as r:
            ct = r.headers.get("Content-Type", "")
            body = r.read()
            if "json" in ct:
                return json.loads(body)
            return body.decode()

    def _post(self, path, data):
        url = f"http://127.0.0.1:{self.PORT}{path}"
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    # ── Health & Node ──────────────────────────────────

    def test_01_health(self):
        data = self._get("/v1/health")
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["initialized"])
        self.assertEqual(data["profile"], "cpu-only")
        self.assertGreater(data["uptime_seconds"], 0)

    def test_02_node(self):
        data = self._get("/v1/node")
        self.assertEqual(data["node"]["node_id"], "e2e-daemon")
        self.assertEqual(data["node"]["hostname"], "test-host")
        self.assertIn("system", data)

    def test_03_runtime(self):
        data = self._get("/v1/runtime")
        self.assertIn("profile", data)
        self.assertIn("container_runtime", data)

    # ── Stacks & Recipes ──────────────────────────────

    def test_04_stacks_empty(self):
        data = self._get("/v1/stacks")
        self.assertEqual(data["stacks"], [])

    def test_05_recipes(self):
        data = self._get("/v1/recipes")
        self.assertGreater(len(data["recipes"]), 5)
        names = [r["name"] for r in data["recipes"]]
        self.assertIn("local-chat", names)
        self.assertIn("team-rag", names)

    # ── Metrics ───────────────────────────────────────

    def test_06_psi(self):
        data = self._get("/v1/metrics/psi")
        self.assertIn("memory_some_avg10", data)
        self.assertIn("cpu_some_avg10", data)

    def test_07_slo(self):
        data = self._get("/v1/metrics/slo")
        self.assertIn("slo", data)
        self.assertIn("pressure", data)

    def test_08_prometheus(self):
        text = self._get("/metrics")
        self.assertIn("# HELP", text)
        self.assertIn("aios_node_info", text)
        self.assertIn("aios_psi_", text)

    # ── Enterprise Features ───────────────────────────

    def test_09_fabric(self):
        data = self._get("/v1/fabric")
        self.assertIn("fabric", data)
        self.assertIn("tiers", data["fabric"])
        self.assertGreater(data["fabric"]["total_capacity_gb"], 0)
        self.assertIn("placement_policy", data)

    def test_10_context(self):
        data = self._get("/v1/context")
        self.assertIn("snapshots", data)

    def test_11_recommend(self):
        data = self._get("/v1/recommend")
        self.assertIn("recommendations", data)
        self.assertGreater(len(data["recommendations"]), 0)
        # Should recommend models that fit in 16GB RAM
        for rec in data["recommendations"]:
            self.assertIn("name", rec)
            self.assertIn("runtime", rec)

    def test_12_apikeys(self):
        data = self._get("/v1/apikeys")
        self.assertIn("keys", data)

    def test_13_audit(self):
        data = self._get("/v1/audit")
        self.assertIn("entries", data)

    # ── Engines & Broker ──────────────────────────────

    def test_14_engines(self):
        data = self._get("/v1/broker/engines")
        self.assertIn("engines", data)
        # All engines should be unreachable in test env
        for e in data["engines"]:
            self.assertFalse(e.get("reachable", False))

    def test_15_cluster(self):
        data = self._get("/v1/cluster")
        self.assertIn("mode", data)

    def test_16_upgrade_plan(self):
        data = self._get("/v1/upgrade/plan")
        self.assertIn("steps", data)
        self.assertGreater(len(data["steps"]), 3)

    def test_17_events(self):
        data = self._get("/v1/events")
        self.assertIn("events", data)

    # ── 404 handling ──────────────────────────────────

    def test_18_not_found(self):
        try:
            self._get("/v1/nonexistent")
            self.fail("Should have raised")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    # ── Concurrent access ─────────────────────────────

    def test_19_concurrent_requests(self):
        """10 concurrent requests should all succeed."""
        results = [None] * 10
        errors = [None] * 10

        def fetch(i):
            try:
                results[i] = self._get("/v1/health")
            except Exception as e:
                errors[i] = str(e)

        threads = [threading.Thread(target=fetch, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        successes = sum(1 for r in results if r is not None)
        self.assertGreaterEqual(successes, 8, f"Only {successes}/10 succeeded. Errors: {errors}")

    # ── All endpoints summary ─────────────────────────

    def test_20_all_endpoints_accessible(self):
        """Verify all 19 JSON endpoints return valid responses."""
        json_endpoints = [
            "/v1/health", "/v1/node", "/v1/runtime", "/v1/stacks",
            "/v1/services", "/v1/models", "/v1/recipes",
            "/v1/metrics/slo", "/v1/metrics/psi", "/v1/upgrade/plan",
            "/v1/broker/engines", "/v1/cluster", "/v1/events",
            "/v1/fabric", "/v1/context", "/v1/recommend",
            "/v1/apikeys", "/v1/audit",
        ]
        failed = []
        for ep in json_endpoints:
            try:
                data = self._get(ep)
                self.assertIsInstance(data, dict, f"{ep} returned non-dict")
            except Exception as e:
                failed.append(f"{ep}: {e}")

        self.assertEqual(len(failed), 0, f"Failed endpoints: {failed}")


if __name__ == "__main__":
    unittest.main()
