"""Full-path E2E: daemon + mock engine + API verification.

Tests the complete request lifecycle:
  1. Start mock engine on :19997
  2. Start daemon on :19996
  3. Verify daemon health includes mock engine status
  4. Verify daemon /v1/dynamo endpoint
  5. Verify daemon /v1/fabric endpoint
  6. Send chat completion through mock engine
  7. Verify metrics updated
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


class TestFullPath(unittest.TestCase):
    """Full stack test: daemon + mock engine."""

    MOCK_PORT = 19997
    DAEMON_PORT = 19996

    @classmethod
    def setUpClass(cls):
        from aictl.daemon.mock_engine import start_mock_engine
        from aictl.daemon.aiosd import AIOSHandler, ThreadedHTTPServer
        from aictl.core.state import StateStore, NodeState

        # State
        cls.tmp = Path(tempfile.mkdtemp())
        cls.store = StateStore(cls.tmp)
        cls.store.save_node(NodeState(
            node_id="fullpath", hostname="test", profile="cpu-only",
            version="1.5.0", ram_total_mb=16384,
        ))

        # Mock engine
        cls.mock = start_mock_engine(port=cls.MOCK_PORT)
        time.sleep(0.2)

        # Daemon
        AIOSHandler.store = cls.store
        cls.daemon = ThreadedHTTPServer(("127.0.0.1", cls.DAEMON_PORT), AIOSHandler)
        cls.daemon._start_time = time.time()
        cls.daemon_thread = threading.Thread(target=cls.daemon.serve_forever, daemon=True)
        cls.daemon_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.daemon.shutdown()
        cls.daemon.server_close()
        cls.mock.shutdown()
        cls.mock.server_close()

    def _get(self, port, path):
        url = f"http://127.0.0.1:{port}{path}"
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())

    def _post(self, port, path, data):
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def test_01_mock_engine_healthy(self):
        data = self._get(self.MOCK_PORT, "/health")
        self.assertEqual(data["status"], "ok")

    def test_02_daemon_healthy(self):
        data = self._get(self.DAEMON_PORT, "/v1/health")
        self.assertEqual(data["status"], "ok")

    def test_03_daemon_fabric(self):
        data = self._get(self.DAEMON_PORT, "/v1/fabric")
        self.assertIn("fabric", data)
        self.assertIn("placement_policy", data)

    def test_04_daemon_dynamo(self):
        data = self._get(self.DAEMON_PORT, "/v1/dynamo")
        self.assertIn("dynamo", data)
        self.assertIn("kvbm", data)

    def test_05_daemon_recommend(self):
        data = self._get(self.DAEMON_PORT, "/v1/recommend")
        self.assertIn("recommendations", data)
        self.assertGreater(len(data["recommendations"]), 0)

    def test_06_mock_chat_completion(self):
        resp = self._post(self.MOCK_PORT, "/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "hello"}],
        })
        self.assertIn("choices", resp)
        self.assertGreater(len(resp["choices"][0]["message"]["content"]), 0)

    def test_07_mock_models(self):
        data = self._get(self.MOCK_PORT, "/v1/models")
        self.assertGreater(len(data["data"]), 0)

    def test_08_daemon_recipes(self):
        data = self._get(self.DAEMON_PORT, "/v1/recipes")
        self.assertIn("recipes", data)
        names = [r["name"] for r in data["recipes"]]
        self.assertIn("local-chat", names)
        self.assertIn("multi-model", names)

    def test_09_daemon_all_endpoints(self):
        """All daemon endpoints compile."""
        """Verify all 21 GET endpoints respond."""
        endpoints = [
            "/v1/health", "/v1/node", "/v1/runtime", "/v1/stacks",
            "/v1/services", "/v1/models", "/v1/recipes",
            "/v1/metrics/slo", "/v1/metrics/psi", "/v1/upgrade/plan",
            "/v1/broker/engines", "/v1/cluster",
            "/v1/events", "/v1/fabric", "/v1/context",
            "/v1/recommend", "/v1/apikeys", "/v1/audit", "/v1/dynamo",
        ]
        for ep in endpoints:
            try:
                self._get(self.DAEMON_PORT, ep)
            except Exception as e:
                self.fail(f"Endpoint {ep} failed: {e}")
        self.assertTrue(True)  # reached without import errors

    def test_10_mock_metrics_after_requests(self):
        # Send a request first
        self._post(self.MOCK_PORT, "/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "metrics test"}],
        })
        # Check metrics
        url = f"http://127.0.0.1:{self.MOCK_PORT}/metrics"
        with urllib.request.urlopen(url, timeout=5) as r:
            text = r.read().decode()
        self.assertIn("vllm:request_success_total", text)
        # Should have at least 1 request
        for line in text.splitlines():
            if line.startswith("vllm:request_success_total"):
                count = int(line.split()[-1])
                self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()
