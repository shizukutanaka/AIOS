"""Tests for K8s Gateway API Inference Extension."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.stack.gateway import (
    stack_to_gateway_api, GatewayInferenceConfig,
)
from aictl.stack.manifest import get_recipe


class TestGatewayAPIInferenceExtension(unittest.TestCase):
    def test_local_chat_generates_resources(self):
        manifest = get_recipe("local-chat")
        resources = stack_to_gateway_api(manifest)
        kinds = [r["kind"] for r in resources]
        self.assertIn("InferencePool", kinds)
        self.assertIn("InferenceModel", kinds)
        self.assertIn("Gateway", kinds)
        self.assertIn("HTTPRoute", kinds)

    def test_inference_pool_v1(self):
        manifest = get_recipe("team-rag")
        resources = stack_to_gateway_api(manifest)
        pools = [r for r in resources if r["kind"] == "InferencePool"]
        self.assertGreater(len(pools), 0)
        pool = pools[0]
        self.assertEqual(pool["apiVersion"], "inference.networking.k8s.io/v1")
        self.assertIn("targetPorts", pool["spec"])
        self.assertIn("extensionRef", pool["spec"])

    def test_inference_model_criticality(self):
        manifest = get_recipe("team-rag")
        resources = stack_to_gateway_api(manifest)
        models = [r for r in resources if r["kind"] == "InferenceModel"]
        self.assertGreater(len(models), 0)
        # GPU-required services should be Critical
        for m in models:
            self.assertIn(m["spec"]["criticality"], ["Critical", "Standard"])

    def test_gateway_class_config(self):
        manifest = get_recipe("local-chat")
        config = GatewayInferenceConfig(gateway_class="nginx")
        resources = stack_to_gateway_api(manifest, config)
        gw = [r for r in resources if r["kind"] == "Gateway"][0]
        self.assertEqual(gw["spec"]["gatewayClassName"], "nginx")

    def test_httproute_references_pools(self):
        manifest = get_recipe("local-chat")
        resources = stack_to_gateway_api(manifest)
        routes = [r for r in resources if r["kind"] == "HTTPRoute"]
        self.assertEqual(len(routes), 1)
        backends = routes[0]["spec"]["rules"][0]["backendRefs"]
        self.assertTrue(all(b["kind"] == "InferencePool" for b in backends))

    def test_epp_config(self):
        manifest = get_recipe("local-chat")
        config = GatewayInferenceConfig(epp_port=9999)
        resources = stack_to_gateway_api(manifest, config)
        pools = [r for r in resources if r["kind"] == "InferencePool"]
        for pool in pools:
            self.assertEqual(pool["spec"]["extensionRef"]["port"], 9999)

    def test_cli_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["cluster", "gateway", "team-rag", "--class", "nginx"])
        self.assertEqual(args.cluster_cmd, "gateway")
        self.assertEqual(args.stack, "team-rag")
        self.assertEqual(args.gw_class, "nginx")


if __name__ == "__main__":
    unittest.main()
