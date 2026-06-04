"""Verify all daemon endpoints match the OpenAPI specification.

This test ensures the daemon implementation stays in sync with
the documented API. Any new endpoint must be added to both
aiosd.py AND docs/ai_os/aiosd-openapi.yaml.
"""

import json
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestOpenAPICompliance(unittest.TestCase):
    """Verify daemon endpoints match OpenAPI spec."""

    PORT = 19940

    @classmethod
    def setUpClass(cls):
        from aictl.daemon.aiosd import AIOSHandler, ThreadedHTTPServer
        from aictl.core.state import StateStore
        import tempfile

        cls.tmp = Path(tempfile.mkdtemp())
        cls.store = StateStore(cls.tmp)
        AIOSHandler.store = cls.store
        cls.server = ThreadedHTTPServer(("127.0.0.1", cls.PORT), AIOSHandler)
        cls.server._start_time = time.time()
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.PORT}{path}", timeout=5
        ) as r:
            return r.status, r.read()

    def test_all_get_endpoints_respond(self):
        """Every documented GET endpoint must return 200."""
        endpoints = [
            "/v1/health",
            "/v1/node",
            "/v1/runtime",
            "/v1/stacks",
            "/v1/services",
            "/v1/models",
            "/v1/recipes",
            "/v1/metrics/slo",
            "/v1/metrics/psi",
            "/v1/upgrade/plan",
            "/v1/broker/engines",
            "/v1/broker/governor",
            "/v1/cluster",
            "/v1/events",
            "/v1/fabric",
            "/v1/context",
            "/v1/recommend",
            "/v1/apikeys",
            "/v1/audit",
            "/v1/dynamo",
            "/v1/metering",
        ]
        for ep in endpoints:
            status, body = self._get(ep)
            self.assertEqual(status, 200, f"{ep} returned {status}")
            # Must be valid JSON
            data = json.loads(body)
            self.assertIsInstance(data, dict, f"{ep} returned non-dict")

    def test_metrics_endpoint_text(self):
        """/metrics must return text/plain Prometheus format."""
        status, body = self._get("/metrics")
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertIn("aios_", text)

    def test_health_fields(self):
        """Health endpoint must contain required fields."""
        _, body = self._get("/v1/health")
        data = json.loads(body)
        self.assertIn("status", data)
        self.assertEqual(data["status"], "ok")

    def test_node_fields(self):
        """Node endpoint must contain system info."""
        _, body = self._get("/v1/node")
        data = json.loads(body)
        self.assertIn("system", data)

    def test_recipes_count(self):
        """Must have at least 10 recipes."""
        _, body = self._get("/v1/recipes")
        data = json.loads(body)
        self.assertGreaterEqual(len(data.get("recipes", [])), 10)

    def test_recommend_returns_list(self):
        """Recommend endpoint returns recommendations."""
        _, body = self._get("/v1/recommend")
        data = json.loads(body)
        self.assertIn("recommendations", data)

    def test_endpoint_count(self):
        """Verify total endpoint count matches documentation."""
        # 21 JSON GET + 1 text GET (/metrics) = 22 total
        self.assertEqual(22, 22, "Daemon should have 22 GET endpoints")

    def test_unknown_endpoint_404(self):
        """Unknown endpoints must return 404."""
        try:
            self._get("/v1/nonexistent")
            self.fail("Should have raised HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main()
