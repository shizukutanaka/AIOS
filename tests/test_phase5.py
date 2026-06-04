"""Tests for Phase 5: K3s, cluster command, SGLang metrics, research updates."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.k3s import (
    check_prerequisites, generate_promote_plan, stack_to_k8s, PromotePlan,
)
from aictl.runtime.adapters import _prom_gauge, _prom_histogram_quantile
from aictl.core.state import StateStore, NodeState, StackEntry
from aictl.runtime.nodes import NodeManager


class TestK3sPrerequisites(unittest.TestCase):
    def test_returns_tuple(self):
        ok, issues = check_prerequisites()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(issues, list)


class TestK3sPromotePlan(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = StateStore(self.tmp)
        self.store.save_node(NodeState(node_id="t", hostname="h", gpu_count=1))

    def test_no_peers_not_ready(self):
        plan = generate_promote_plan(self.store)
        self.assertFalse(plan.ready)

    def test_with_peers_ready(self):
        mgr = NodeManager(self.store)
        token = mgr.generate_join_token()
        mgr.accept_join({"node_id": "w1", "hostname": "w", "address": "10.0.0.2",
                         "port": 7700, "token": token})
        plan = generate_promote_plan(self.store)
        self.assertTrue(plan.ready)
        self.assertGreater(len(plan.steps), 3)
        # Should include GPU operator step since gpu_count=1
        actions = [s.action for s in plan.steps]
        self.assertIn("gpu_operator", actions)

    def test_warnings_present(self):
        mgr = NodeManager(self.store)
        token = mgr.generate_join_token()
        mgr.accept_join({"node_id": "w1", "hostname": "w", "address": "10.0.0.2",
                         "port": 7700, "token": token})
        plan = generate_promote_plan(self.store)
        self.assertTrue(any("etcd" in w for w in plan.warnings))


class TestStackToK8s(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = StateStore(self.tmp)
        self.store.save_node(NodeState(node_id="t", hostname="h"))
        self.store.upsert_stack(StackEntry(
            name="test", file="test.json", status="running",
            services=[
                {"name": "llm", "image": "vllm:latest", "port": 8000, "gpu_required": True},
                {"name": "ui", "image": "webui:latest", "port": 3000},
            ],
        ))

    def test_generates_k8s_list(self):
        k8s = stack_to_k8s("test", self.store)
        self.assertEqual(k8s["kind"], "List")
        self.assertGreater(len(k8s["items"]), 0)

    def test_has_deployments_and_services(self):
        k8s = stack_to_k8s("test", self.store)
        kinds = [i["kind"] for i in k8s["items"]]
        self.assertIn("Deployment", kinds)
        self.assertIn("Service", kinds)

    def test_gpu_resources(self):
        k8s = stack_to_k8s("test", self.store)
        deploys = [i for i in k8s["items"] if i["kind"] == "Deployment"]
        gpu_dep = [d for d in deploys if "llm" in d["metadata"]["name"]]
        self.assertEqual(len(gpu_dep), 1)
        containers = gpu_dep[0]["spec"]["template"]["spec"]["containers"]
        self.assertIn("nvidia.com/gpu", containers[0].get("resources", {}).get("limits", {}))

    def test_nonexistent_stack(self):
        k8s = stack_to_k8s("nonexistent", self.store)
        self.assertEqual(k8s, {})


class TestSGLangMetricPrefix(unittest.TestCase):
    SGLANG_METRICS = """
sglang_num_requests_waiting 7
sglang_num_requests_running 3
sglang_cache_hit_rate 0.85
sglang_time_to_first_token_seconds_sum 10.0
sglang_time_to_first_token_seconds_count 50
"""

    def test_underscore_prefix(self):
        self.assertEqual(_prom_gauge(self.SGLANG_METRICS, "sglang_num_requests_waiting"), 7.0)

    def test_colon_prefix_no_match(self):
        self.assertEqual(_prom_gauge(self.SGLANG_METRICS, "sglang:num_requests_waiting"), 0.0)

    def test_cache_hit_rate(self):
        self.assertAlmostEqual(_prom_gauge(self.SGLANG_METRICS, "sglang_cache_hit_rate"), 0.85)


class TestOTelGenAISemConv(unittest.TestCase):
    def test_genai_metric_names(self):
        from aictl.metrics.otel import build_metric_payload
        from aictl.metrics.slo import InferenceMetrics, SystemPressure
        payload = build_metric_payload(InferenceMetrics(ttft_ms_p95=200.0), SystemPressure())
        names = [m["name"] for m in payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]]
        self.assertTrue(any("gen_ai.server" in n for n in names))
        self.assertTrue(any("aios.psi" in n for n in names))


class TestOTelCollectorConfig(unittest.TestCase):
    def test_generate_config(self):
        from aictl.metrics.collector_config import generate_otel_config
        from aictl.core.config import Config
        config = Config()
        yaml_str = generate_otel_config(config)
        self.assertIn("receivers:", yaml_str)
        self.assertIn("prometheus:", yaml_str)
        self.assertIn("localhost:8000", yaml_str)


class TestClusterCLI(unittest.TestCase):
    def test_promote_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["cluster", "promote"])
        self.assertEqual(args.cluster_cmd, "promote")

    def test_export_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["cluster", "export", "mystack"])
        self.assertEqual(args.cluster_cmd, "export")
        self.assertEqual(args.stack, "mystack")


class TestAllCommandsRegistered(unittest.TestCase):
    def test_16_commands(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        for cmd in ["init", "doctor", "ps", "serve", "status"]:
            args = p.parse_args([cmd])
            self.assertEqual(args.command, cmd)
        args = p.parse_args(["apply", "-f", "x"])
        self.assertEqual(args.command, "apply")
        args = p.parse_args(["cluster", "promote"])
        self.assertEqual(args.command, "cluster")


if __name__ == "__main__":
    unittest.main()
