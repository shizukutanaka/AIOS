"""Tests for Quadlet generator and aiosd daemon API."""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.stack.manifest import get_recipe, _build_manifest, StackManifest, ServiceDef
from aictl.stack.quadlet import generate_quadlets, QuadletUnit


class TestQuadletGenerator(unittest.TestCase):
    def test_local_chat_generates_webui_only(self):
        """local-chat: ollama runs natively, only webui gets a Quadlet."""
        manifest = get_recipe("local-chat")
        units = generate_quadlets(manifest)
        # Ollama service has no image and runtime=ollama → skipped
        # webui has an image → gets a Quadlet
        names = [u.filename for u in units]
        self.assertTrue(any("webui" in n for n in names))
        # Ollama without explicit image should be skipped
        ollama_units = [u for u in units if "llm" in u.filename]
        self.assertEqual(len(ollama_units), 0)

    def test_team_rag_generates_gpu_flags(self):
        """team-rag: vllm service should have GPU device passthrough."""
        manifest = get_recipe("team-rag")
        units = generate_quadlets(manifest)
        vllm_units = [u for u in units if "llm" in u.filename]
        self.assertEqual(len(vllm_units), 1)
        self.assertIn("nvidia.com/gpu=all", vllm_units[0].content)
        self.assertIn("ShmSize=1g", vllm_units[0].content)

    def test_unit_has_required_sections(self):
        manifest = get_recipe("team-rag")
        units = generate_quadlets(manifest)
        for u in units:
            self.assertIn("[Unit]", u.content)
            self.assertIn("[Container]", u.content)
            self.assertIn("[Service]", u.content)
            self.assertIn("[Install]", u.content)

    def test_health_check_present(self):
        manifest = _build_manifest({
            "name": "test",
            "services": [
                {"name": "api", "image": "test:latest", "port": 8080, "health_path": "/healthz"}
            ],
        }, "test")
        units = generate_quadlets(manifest)
        self.assertEqual(len(units), 1)
        self.assertIn("HealthCmd=", units[0].content)
        self.assertIn("/healthz", units[0].content)

    def test_labels_present(self):
        manifest = _build_manifest({
            "name": "mystack",
            "services": [{"name": "svc", "image": "img:latest"}],
        }, "test")
        units = generate_quadlets(manifest)
        self.assertIn("aios.stack=mystack", units[0].content)
        self.assertIn("aios.service=svc", units[0].content)

    def test_auto_update_enabled(self):
        manifest = _build_manifest({
            "name": "test",
            "services": [{"name": "svc", "image": "img:latest"}],
        }, "test")
        units = generate_quadlets(manifest)
        self.assertIn("AutoUpdate=registry", units[0].content)

    def test_memory_limit_set(self):
        manifest = _build_manifest({
            "name": "test",
            "services": [
                {"name": "llm", "image": "vllm:latest", "runtime": "vllm",
                 "gpu_required": True, "gpu_memory_mb": 16384}
            ],
        }, "test")
        units = generate_quadlets(manifest)
        # 16384 + 2048 overhead = 18432
        self.assertIn("MemoryMax=18432M", units[0].content)

    def test_env_vars_in_unit(self):
        manifest = _build_manifest({
            "name": "test",
            "services": [
                {"name": "ui", "image": "ui:latest",
                 "env": {"API_URL": "http://localhost:8000", "DEBUG": "false"}}
            ],
        }, "test")
        units = generate_quadlets(manifest)
        self.assertIn("Environment=API_URL=http://localhost:8000", units[0].content)
        self.assertIn("Environment=DEBUG=false", units[0].content)

    def test_vllm_model_arg(self):
        manifest = _build_manifest({
            "name": "test",
            "services": [
                {"name": "llm", "runtime": "vllm",
                 "model": "meta-llama/Llama-3.2-8B", "port": 8000}
            ],
        }, "test")
        units = generate_quadlets(manifest)
        self.assertIn("--model meta-llama/Llama-3.2-8B", units[0].content)

    def test_service_dependencies(self):
        """WebUI should depend on backend services."""
        manifest = _build_manifest({
            "name": "test",
            "services": [
                {"name": "llm", "runtime": "vllm", "image": "vllm:latest",
                 "model": "m", "gpu_required": True},
                {"name": "ui", "image": "ui:latest", "port": 3000},
            ],
        }, "test")
        units = generate_quadlets(manifest)
        ui_unit = [u for u in units if "ui" in u.filename][0]
        self.assertIn("After=aios-test-llm.service", ui_unit.content)


class TestDaemonAPI(unittest.TestCase):
    """Integration test for aiosd HTTP API."""

    @classmethod
    def setUpClass(cls):
        from aictl.daemon.aiosd import AIOSHandler, DEFAULT_HOST
        from http.server import HTTPServer
        from aictl.core.state import StateStore

        cls.tmp = tempfile.mkdtemp()
        store = StateStore(Path(cls.tmp))
        AIOSHandler.store = store

        cls.port = 17700  # Use high port for test
        cls.server = HTTPServer((DEFAULT_HOST, cls.port), AIOSHandler)
        cls.server._start_time = time.time()
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str) -> dict:
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())

    def _post(self, path: str, body: dict) -> dict:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                    headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def test_health(self):
        data = self._get("/v1/health")
        self.assertEqual(data["status"], "ok")
        self.assertIn("profile", data)

    def test_node_status(self):
        data = self._get("/v1/node")
        self.assertIn("node", data)
        self.assertIn("system", data)

    def test_runtime_info(self):
        data = self._get("/v1/runtime")
        self.assertIn("profile", data)
        self.assertIn("container_runtime", data)

    def test_list_stacks_empty(self):
        data = self._get("/v1/stacks")
        self.assertIsInstance(data["stacks"], list)

    def test_list_recipes(self):
        data = self._get("/v1/recipes")
        names = [r["name"] for r in data["recipes"]]
        self.assertIn("local-chat", names)

    def test_slo_status(self):
        data = self._get("/v1/metrics/slo")
        self.assertIn("slo", data)
        self.assertIn("compliant", data["slo"])

    def test_psi_status(self):
        data = self._get("/v1/metrics/psi")
        self.assertIn("memory_some_avg10", data)

    def test_register_model(self):
        data = self._post("/v1/models/register", {"name": "test-model", "format": "gguf"})
        self.assertIn("id", data)
        self.assertEqual(data["name"], "test-model")

        models = self._get("/v1/models")
        self.assertTrue(any(m["name"] == "test-model" for m in models["models"]))

    def test_404(self):
        try:
            self._get("/v1/nonexistent")
            self.fail("Should have raised")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_upgrade_plan(self):
        data = self._get("/v1/upgrade/plan")
        self.assertIn("steps", data)
        self.assertIn("rollback", data)


if __name__ == "__main__":
    unittest.main()


class TestQuadletValidation(unittest.TestCase):
    """Validate generated Quadlet files."""

    def test_ollama_example_valid(self):
        from aictl.stack.quadlet import validate_quadlet
        content = Path("/home/claude/aios/examples/ollama.container").read_text()
        issues = validate_quadlet(content)
        # Should have no critical issues (Image, Container sections present)
        critical = [i for i in issues if "Missing [Container]" in i or "Missing Image=" in i]
        self.assertEqual(len(critical), 0, f"Critical issues: {critical}")

    def test_open_webui_example_valid(self):
        from aictl.stack.quadlet import validate_quadlet
        content = Path("/home/claude/aios/examples/open-webui.container").read_text()
        issues = validate_quadlet(content)
        critical = [i for i in issues if "Missing [Container]" in i or "Missing Image=" in i]
        self.assertEqual(len(critical), 0)

    def test_generated_quadlet_valid(self):
        from aictl.stack.quadlet import generate_quadlets, validate_quadlet
        from aictl.stack.manifest import get_recipe
        manifest = get_recipe("local-chat")
        units = generate_quadlets(manifest)
        for unit in units:
            issues = validate_quadlet(unit.content)
            self.assertFalse(
                any("Missing [Container]" in i or "Missing Image=" in i for i in issues),
                f"Unit {unit.filename}: {issues}"
            )

    def test_empty_quadlet_invalid(self):
        from aictl.stack.quadlet import validate_quadlet
        issues = validate_quadlet("")
        self.assertGreater(len(issues), 0)

    def test_minimal_quadlet_valid(self):
        from aictl.stack.quadlet import validate_quadlet
        content = """[Container]
Image=docker.io/ollama/ollama
ContainerName=test

[Service]
Restart=always

[Install]
WantedBy=default.target
"""
        issues = validate_quadlet(content)
        # Should only have non-critical warnings
        critical = [i for i in issues if "Missing [Container]" in i or "Missing Image=" in i]
        self.assertEqual(len(critical), 0)
