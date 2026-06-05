"""Tests for NVIDIA Dynamo integration, DGDR, KVBM."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.dynamo import (
    KVBMConfig, DGDRSpec, detect_dynamo,
    generate_kvbm_config, generate_dgdr_yaml, estimate_dgdr_resources,
)


class TestKVBMConfig(unittest.TestCase):
    def test_defaults(self):
        c = KVBMConfig()
        self.assertEqual(c.block_size_tokens, 16)
        self.assertEqual(c.eviction_policy, "lru")
        self.assertEqual(c.nixl_backend, "tcp")

    def test_generate_from_fabric(self):
        config = generate_kvbm_config()
        self.assertIsInstance(config, KVBMConfig)
        # Should detect at least some DRAM
        self.assertGreater(config.cpu_dram_gb, 0)


class TestDGDR(unittest.TestCase):
    def test_spec_defaults(self):
        spec = DGDRSpec(model="llama3.1:8b")
        self.assertEqual(spec.sla_ttft_ms, 500)
        self.assertEqual(spec.max_gpus, 8)

    def test_generate_yaml(self):
        spec = DGDRSpec(model="meta-llama/Llama-3.2-8B-Instruct")
        manifest = generate_dgdr_yaml(spec)
        self.assertEqual(manifest["kind"], "InferenceDeployment")
        self.assertEqual(manifest["spec"]["model"], "meta-llama/Llama-3.2-8B-Instruct")
        self.assertTrue(manifest["spec"]["features"]["kvbm"]["enabled"])
        self.assertTrue(manifest["spec"]["features"]["modelExpress"]["enabled"])

    def test_estimate_8b(self):
        spec = DGDRSpec(model="llama3.1:8b", hardware="RTX4090", quantization="fp16")
        est = estimate_dgdr_resources(spec)
        self.assertEqual(est["model_params_b"], 8)
        self.assertGreater(est["model_vram_gb"], 0)
        self.assertEqual(est["gpus_needed"], 1)  # 8B fp16 = 16GB, fits in 24GB RTX4090

    def test_estimate_70b(self):
        spec = DGDRSpec(model="llama3.1:70b", hardware="H100", quantization="fp16")
        est = estimate_dgdr_resources(spec)
        self.assertEqual(est["model_params_b"], 70)
        self.assertGreater(est["gpus_needed"], 1)  # 70B fp16 = 140GB > 80GB
        self.assertTrue(est["disagg_recommended"])

    def test_estimate_auto_quant(self):
        spec = DGDRSpec(model="gemma4:27b", quantization="auto")
        est = estimate_dgdr_resources(spec)
        self.assertEqual(est["model_params_b"], 27)

    def test_cost_constraint(self):
        spec = DGDRSpec(model="llama3.1:8b", max_cost_per_hour=5.0)
        manifest = generate_dgdr_yaml(spec)
        self.assertEqual(manifest["spec"]["constraints"]["maxCostPerHour"], 5.0)

    def test_disagg_flag(self):
        spec = DGDRSpec(model="llama3.1:8b", disagg=True)
        manifest = generate_dgdr_yaml(spec)
        self.assertTrue(manifest["spec"]["features"]["disaggregation"])


class TestDynamoDetect(unittest.TestCase):
    def test_detect(self):
        status = detect_dynamo()
        self.assertIn("dynamo_available", status)
        self.assertIn("nixl_available", status)
        self.assertIn("grove_available", status)


class TestDeployCLI(unittest.TestCase):
    def test_plan_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["deploy", "plan", "llama3.1:8b", "--hardware", "H100", "--ttft", "200"])
        self.assertEqual(args.deploy_cmd, "plan")
        self.assertEqual(args.model, "llama3.1:8b")
        self.assertEqual(args.ttft, 200)

    def test_manifest_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["deploy", "manifest", "gemma4:27b"])
        self.assertEqual(args.deploy_cmd, "manifest")

    def test_dynamo_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["deploy", "dynamo"])
        self.assertEqual(args.deploy_cmd, "dynamo")

    def test_kvbm_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["deploy", "kvbm"])
        self.assertEqual(args.deploy_cmd, "kvbm")


if __name__ == "__main__":
    unittest.main()
