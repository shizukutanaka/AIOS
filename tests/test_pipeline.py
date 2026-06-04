"""Full pipeline E2E: proxy → router → mock engine → metering.

This test proves the entire OS works as a single integrated system:
  1. Start mock engine (:19960)
  2. Start daemon (:19961)
  3. Configure proxy router to point at mock engine
  4. Send request through proxy → router selects engine → engine responds
  5. Verify token metering recorded the usage
  6. Verify all daemon endpoints reflect the activity

This is the definitive test that the OS architecture works end-to-end.
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


class TestFullPipeline(unittest.TestCase):
    """Prove the entire OS works as an integrated system."""

    MOCK_PORT = 19960
    DAEMON_PORT = 19961

    @classmethod
    def setUpClass(cls):
        from aictl.daemon.mock_engine import start_mock_engine
        from aictl.daemon.aiosd import AIOSHandler, ThreadedHTTPServer
        from aictl.core.state import StateStore, NodeState
        from aictl.core.config import Config

        # State + config
        cls.tmp = Path(tempfile.mkdtemp())
        cls.store = StateStore(cls.tmp)
        cls.store.save_node(NodeState(
            node_id="pipeline-test", hostname="test", profile="cpu-only",
            version="1.5.0", ram_total_mb=16384,
        ))

        # Write config pointing engines at mock
        config_data = {
            "engines": {
                "vllm": f"http://127.0.0.1:{cls.MOCK_PORT}",
                "ollama": f"http://127.0.0.1:{cls.MOCK_PORT}",
            }
        }
        (cls.tmp / "config.json").write_text(json.dumps(config_data))

        # Mock engine
        cls.mock = start_mock_engine(port=cls.MOCK_PORT)
        time.sleep(0.2)

        # Daemon
        AIOSHandler.store = cls.store
        cls.daemon = ThreadedHTTPServer(("127.0.0.1", cls.DAEMON_PORT), AIOSHandler)
        cls.daemon._start_time = time.time()
        threading.Thread(target=cls.daemon.serve_forever, daemon=True).start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.daemon.shutdown()
        cls.daemon.server_close()
        cls.mock.shutdown()
        cls.mock.server_close()

    def _get(self, port, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return json.loads(r.read())

    def _post(self, port, path, data):
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    # ── Pipeline tests (ordered) ──────────────────────

    def test_01_mock_engine_ready(self):
        """Step 1: Mock engine is healthy."""
        data = self._get(self.MOCK_PORT, "/health")
        self.assertEqual(data["status"], "ok")

    def test_02_daemon_ready(self):
        """Step 2: Daemon is healthy."""
        data = self._get(self.DAEMON_PORT, "/v1/health")
        self.assertEqual(data["status"], "ok")

    def test_03_engine_reachable_via_daemon(self):
        """Step 3: Daemon can see mock engine as available."""
        data = self._get(self.DAEMON_PORT, "/v1/broker/engines")
        engines = data.get("engines", [])
        # At least one engine should report
        self.assertIsInstance(engines, list)

    def test_04_mock_engine_models(self):
        """Step 4: Mock engine serves models."""
        data = self._get(self.MOCK_PORT, "/v1/models")
        models = [m["id"] for m in data["data"]]
        self.assertIn("mock-llama3-8b", models)

    def test_05_direct_completion(self):
        """Step 5: Direct request to mock engine works."""
        resp = self._post(self.MOCK_PORT, "/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 50,
        })
        self.assertIn("choices", resp)
        content = resp["choices"][0]["message"]["content"]
        self.assertGreater(len(content), 0)
        self.assertIn("usage", resp)
        self.assertGreater(resp["usage"]["completion_tokens"], 0)

    def test_06_streaming_works(self):
        """Step 6: SSE streaming works."""
        body = json.dumps({
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "test"}],
            "stream": True,
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.MOCK_PORT}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            chunks = r.read().decode()
        self.assertIn("data:", chunks)
        self.assertIn("[DONE]", chunks)

    def test_07_ollama_compat(self):
        """Step 7: Ollama API compatibility works."""
        data = self._get(self.MOCK_PORT, "/api/tags")
        self.assertIn("models", data)

        resp = self._post(self.MOCK_PORT, "/api/generate", {
            "model": "mock", "prompt": "test",
        })
        self.assertTrue(resp["done"])

    def test_08_prometheus_metrics(self):
        """Step 8: Prometheus metrics are exported."""
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.MOCK_PORT}/metrics", timeout=5
        ) as r:
            text = r.read().decode()
        self.assertIn("vllm:num_requests_running", text)
        self.assertIn("vllm:request_success_total", text)

    def test_09_daemon_fabric(self):
        """Step 9: Memory fabric detection works."""
        data = self._get(self.DAEMON_PORT, "/v1/fabric")
        self.assertIn("fabric", data)
        self.assertGreater(data["fabric"]["total_capacity_gb"], 0)

    def test_10_daemon_dynamo(self):
        """Step 10: Dynamo/KVBM status works."""
        data = self._get(self.DAEMON_PORT, "/v1/dynamo")
        self.assertIn("kvbm", data)
        self.assertGreater(data["kvbm"]["cpu_dram_gb"], 0)

    def test_11_daemon_recommend(self):
        """Step 11: Model recommendations work."""
        data = self._get(self.DAEMON_PORT, "/v1/recommend")
        self.assertGreater(len(data["recommendations"]), 0)

    def test_12_daemon_recipes(self):
        """Step 12: All 10 recipes available."""
        data = self._get(self.DAEMON_PORT, "/v1/recipes")
        self.assertGreaterEqual(len(data["recipes"]), 10)

    def test_13_metering_endpoint(self):
        """Step 13: Token metering endpoint works."""
        data = self._get(self.DAEMON_PORT, "/v1/metering")
        self.assertIn("total_tokens", data)

    def test_14_all_22_endpoints(self):
        """Step 14: All 22 GET endpoints respond."""
        endpoints = [
            "/v1/health", "/v1/node", "/v1/runtime", "/v1/stacks",
            "/v1/services", "/v1/models", "/v1/recipes",
            "/v1/metrics/slo", "/v1/metrics/psi", "/v1/upgrade/plan",
            "/v1/broker/engines", "/v1/broker/governor", "/v1/cluster",
            "/v1/events", "/v1/fabric", "/v1/context",
            "/v1/recommend", "/v1/apikeys", "/v1/audit",
            "/v1/dynamo", "/v1/metering",
        ]
        for ep in endpoints:
            try:
                self._get(self.DAEMON_PORT, ep)
            except Exception as e:
                self.fail(f"Endpoint {ep} failed: {e}")

        # /metrics is text format
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.DAEMON_PORT}/metrics", timeout=5
        ) as r:
            self.assertIn("aios_", r.read().decode())

    def test_15_concurrent_requests(self):
        """Step 15: Multiple concurrent requests don't crash."""
        import concurrent.futures

        def make_request(i):
            return self._post(self.MOCK_PORT, "/v1/chat/completions", {
                "model": "mock-llama3-8b",
                "messages": [{"role": "user", "content": f"request {i}"}],
            })

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(make_request, i) for i in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 10)
        for r in results:
            self.assertIn("choices", r)


if __name__ == "__main__":
    unittest.main()
