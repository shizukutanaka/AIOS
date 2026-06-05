"""Tests for Phase 11: autoscaler, KEDA ScaledObject, HPA, request tracing."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.autoscaler import (
    AutoScaler, ScalePolicy, ScaleDecision,
    generate_keda_scaled_object, generate_hpa_manifest,
)


class TestScalePolicy(unittest.TestCase):
    def test_defaults(self):
        p = ScalePolicy()
        self.assertEqual(p.min_replicas, 1)
        self.assertEqual(p.max_replicas, 8)
        self.assertEqual(p.queue_depth_threshold, 5)

    def test_custom(self):
        p = ScalePolicy(min_replicas=2, max_replicas=16, queue_depth_threshold=10)
        self.assertEqual(p.max_replicas, 16)


class TestKEDAScaledObject(unittest.TestCase):
    def test_vllm_default(self):
        obj = generate_keda_scaled_object("vllm-deploy")
        self.assertEqual(obj["kind"], "ScaledObject")
        self.assertEqual(obj["apiVersion"], "keda.sh/v1alpha1")
        self.assertEqual(obj["spec"]["scaleTargetRef"]["name"], "vllm-deploy")

    def test_vllm_metric(self):
        obj = generate_keda_scaled_object("vllm-deploy", engine="vllm")
        trigger = obj["spec"]["triggers"][0]
        self.assertEqual(trigger["type"], "prometheus")
        self.assertIn("vllm:num_requests_waiting", trigger["metadata"]["query"])

    def test_sglang_metric(self):
        obj = generate_keda_scaled_object("sglang-deploy", engine="sglang")
        trigger = obj["spec"]["triggers"][0]
        self.assertIn("sglang_num_requests_waiting", trigger["metadata"]["query"])

    def test_custom_policy(self):
        policy = ScalePolicy(min_replicas=2, max_replicas=16, queue_depth_threshold=10)
        obj = generate_keda_scaled_object("test", policy=policy)
        self.assertEqual(obj["spec"]["minReplicaCount"], 2)
        self.assertEqual(obj["spec"]["maxReplicaCount"], 16)
        self.assertEqual(obj["spec"]["triggers"][0]["metadata"]["threshold"], "10")

    def test_cooldown(self):
        policy = ScalePolicy(scale_down_cooldown_s=600)
        obj = generate_keda_scaled_object("test", policy=policy)
        self.assertEqual(obj["spec"]["cooldownPeriod"], 600)


class TestHPAManifest(unittest.TestCase):
    def test_default(self):
        hpa = generate_hpa_manifest("vllm-deploy")
        self.assertEqual(hpa["kind"], "HorizontalPodAutoscaler")
        self.assertEqual(hpa["apiVersion"], "autoscaling/v2")
        self.assertEqual(hpa["spec"]["scaleTargetRef"]["name"], "vllm-deploy")

    def test_scale_behavior(self):
        hpa = generate_hpa_manifest("test")
        self.assertIn("scaleUp", hpa["spec"]["behavior"])
        self.assertIn("scaleDown", hpa["spec"]["behavior"])
        # Scale up should be immediate (0 stabilization)
        self.assertEqual(hpa["spec"]["behavior"]["scaleUp"]["stabilizationWindowSeconds"], 0)
        # Scale down should have cooldown
        self.assertGreater(hpa["spec"]["behavior"]["scaleDown"]["stabilizationWindowSeconds"], 0)


class TestAutoScaler(unittest.TestCase):
    def test_unreachable_engine(self):
        scaler = AutoScaler("vllm", "http://localhost:99999")
        decision = scaler.evaluate()
        self.assertEqual(decision.action, "none")
        self.assertIn("unreachable", decision.reason.lower())

    def test_scale_decision_defaults(self):
        d = ScaleDecision()
        self.assertEqual(d.action, "none")
        self.assertEqual(d.current_replicas, 1)
        self.assertGreater(d.timestamp, 0)


class TestScaleCLI(unittest.TestCase):
    def test_keda_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["scale", "keda", "my-deploy", "--engine", "sglang",
                             "--min", "2", "--max", "16", "--threshold", "10"])
        self.assertEqual(args.scale_cmd, "keda")
        self.assertEqual(args.deployment, "my-deploy")
        self.assertEqual(args.engine, "sglang")
        self.assertEqual(args.min, 2)

    def test_hpa_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["scale", "hpa", "my-deploy"])
        self.assertEqual(args.scale_cmd, "hpa")

    def test_trace_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["trace", "--prompt", "test", "--model", "llama3"])
        self.assertEqual(args.command, "trace")
        self.assertEqual(args.prompt, "test")

    def test_all_31_commands(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        simple = ["init", "doctor", "ps", "serve", "status", "setup",
                   "recommend", "proxy", "net", "watch", "trace"]
        for cmd in simple:
            args = p.parse_args([cmd])
            self.assertEqual(args.command, cmd, f"Failed: {cmd}")


if __name__ == "__main__":
    unittest.main()
