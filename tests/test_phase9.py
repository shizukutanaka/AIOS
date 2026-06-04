"""Tests for Phase 9: KServe, proxy auth, threaded server, audit integration."""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.stack.kserve import (
    stack_to_llmisvc, LLMISvcConfig, RUNTIME_IMAGES,
    generate_kserve_install_commands,
)
from aictl.stack.manifest import get_recipe, list_recipes
from aictl.core.audit import AuditLog, AuditEntry, audit
from aictl.core.apikeys import KeyManager
from aictl.core.events import EventBus, Event, emit, get_bus
from aictl.core.state import StateStore, NodeState


class TestKServeLLMISvc(unittest.TestCase):
    def test_team_rag_generates_llmisvc(self):
        manifest = get_recipe("team-rag")
        resources = stack_to_llmisvc(manifest)
        kinds = [r["kind"] for r in resources]
        self.assertIn("LLMInferenceService", kinds)

    def test_llmisvc_has_correct_api_version(self):
        manifest = get_recipe("team-rag")
        resources = stack_to_llmisvc(manifest)
        llmisvc = [r for r in resources if r["kind"] == "LLMInferenceService"]
        self.assertEqual(llmisvc[0]["apiVersion"], "serving.kserve.io/v1alpha1")

    def test_llmisvc_has_model_spec(self):
        manifest = get_recipe("team-rag")
        resources = stack_to_llmisvc(manifest)
        llmisvc = [r for r in resources if r["kind"] == "LLMInferenceService"][0]
        self.assertIn("model", llmisvc["spec"])
        self.assertIn("uri", llmisvc["spec"]["model"])

    def test_llmisvc_has_router_and_scheduler(self):
        manifest = get_recipe("team-rag")
        resources = stack_to_llmisvc(manifest)
        llmisvc = [r for r in resources if r["kind"] == "LLMInferenceService"][0]
        self.assertIn("router", llmisvc["spec"])
        self.assertIn("scheduler", llmisvc["spec"])

    def test_performance_mode(self):
        manifest = get_recipe("local-gpu-chat")
        config = LLMISvcConfig(performance_mode="throughput")
        resources = stack_to_llmisvc(manifest, config)
        llmisvc = [r for r in resources if r["kind"] == "LLMInferenceService"]
        if llmisvc:
            container = llmisvc[0]["spec"]["template"]["containers"][0]
            if "args" in container:
                self.assertIn("--performance-mode", container["args"])
                self.assertIn("throughput", container["args"])

    def test_pd_disaggregation(self):
        manifest = get_recipe("team-rag")
        config = LLMISvcConfig(replicas=6, enable_pd_disagg=True)
        resources = stack_to_llmisvc(manifest, config)
        llmisvc = [r for r in resources if r["kind"] == "LLMInferenceService"]
        if llmisvc:
            self.assertIn("disaggregation", llmisvc[0]["spec"])
            self.assertTrue(llmisvc[0]["spec"]["disaggregation"]["enabled"])

    def test_tensor_parallel(self):
        manifest = get_recipe("team-rag")
        config = LLMISvcConfig(tensor_parallel=2)
        resources = stack_to_llmisvc(manifest, config)
        llmisvc = [r for r in resources if r["kind"] == "LLMInferenceService"]
        if llmisvc:
            self.assertIn("parallelism", llmisvc[0]["spec"])
            self.assertEqual(llmisvc[0]["spec"]["parallelism"]["tensorParallel"], 2)

    def test_non_llm_services_become_deployments(self):
        manifest = get_recipe("team-rag")
        resources = stack_to_llmisvc(manifest)
        deploys = [r for r in resources if r["kind"] == "Deployment"]
        # WebUI and embedding should be regular deployments
        self.assertGreater(len(deploys), 0)

    def test_all_recipes_convert(self):
        for name in list_recipes():
            manifest = get_recipe(name)
            resources = stack_to_llmisvc(manifest)
            self.assertIsInstance(resources, list)

    def test_kserve_install_commands(self):
        cmds = generate_kserve_install_commands()
        self.assertGreater(len(cmds), 3)
        self.assertTrue(any("kserve" in c.lower() for c in cmds))

    def test_labels_present(self):
        manifest = get_recipe("team-rag")
        resources = stack_to_llmisvc(manifest)
        llmisvc = [r for r in resources if r["kind"] == "LLMInferenceService"]
        for r in llmisvc:
            labels = r.get("metadata", {}).get("labels", {})
            self.assertIn("aios.stack", labels)


class TestProxyAuth(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mgr = KeyManager(self.tmp)

    def test_no_keys_allows_all(self):
        """When no keys exist, proxy should be open."""
        keys = self.mgr.list_keys()
        self.assertEqual(len(keys), 0)

    def test_key_validation_flow(self):
        raw, key = self.mgr.generate_key("proxy-test", rate_limit_rpm=10)
        valid, _, found = self.mgr.validate(raw)
        self.assertTrue(valid)
        ok, _ = self.mgr.check_rate_limit(found)
        self.assertTrue(ok)
        self.mgr.record_usage(found.key_id, tokens=50)

        keys = self.mgr.list_keys()
        k = [k for k in keys if k["key_id"] == found.key_id][0]
        self.assertEqual(k["total_tokens"], 50)


class TestAuditIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_audit_writes_and_reads(self):
        log = AuditLog(self.tmp)
        log.write(AuditEntry(event="model.loaded", resource="llama3", action="create"))
        log.write(AuditEntry(event="stack.applied", resource="team-rag", action="apply"))

        entries = log.read(n=10)
        self.assertEqual(len(entries), 2)
        events = {e.event for e in entries}
        self.assertIn("model.loaded", events)
        self.assertIn("stack.applied", events)


class TestThreadedDaemon(unittest.TestCase):
    """Verify the threaded daemon starts and handles concurrent requests."""

    @classmethod
    def setUpClass(cls):
        from aictl.daemon.aiosd import AIOSHandler, ThreadedHTTPServer

        cls.tmp = tempfile.mkdtemp()
        store = StateStore(Path(cls.tmp))
        store.save_node(NodeState(node_id="thread-test", hostname="test"))
        AIOSHandler.store = store

        cls.port = 17703
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
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())

    def test_concurrent_health_checks(self):
        """Multiple concurrent requests should all succeed."""
        results = [None] * 5
        errors = [None] * 5

        def fetch(i):
            try:
                results[i] = self._get("/v1/health")
            except Exception as e:
                errors[i] = str(e)

        threads = [threading.Thread(target=fetch, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        for i, r in enumerate(results):
            self.assertIsNotNone(r, f"Request {i} failed: {errors[i]}")
            self.assertEqual(r["status"], "ok")

    def test_prometheus_and_events(self):
        body = self._get("/v1/events")
        self.assertIn("events", body)


class TestClusterExportKServe(unittest.TestCase):
    def test_export_recipe_as_llmisvc(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["cluster", "export", "team-rag"])
        self.assertEqual(args.stack, "team-rag")


if __name__ == "__main__":
    unittest.main()
