"""Tests for llm-d ModelService Helm values + model DB completeness."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.stack.modelservice import (
    ModelServiceConfig, generate_helm_values, values_to_yaml, PRESETS,
)


class TestModelService(unittest.TestCase):
    def test_basic_values(self):
        config = ModelServiceConfig(model="meta-llama/Llama-3.1-8B-Instruct")
        v = generate_helm_values(config)
        self.assertEqual(v["modelService"]["model"], "meta-llama/Llama-3.1-8B-Instruct")
        self.assertEqual(v["vllmConfig"]["v1"], True)
        self.assertTrue(v["inferencePool"]["enabled"])
        self.assertTrue(v["inferenceModel"]["enabled"])

    def test_presets(self):
        for preset in ["balanced", "latency", "throughput"]:
            config = ModelServiceConfig(model="llama3", preset=preset)
            v = generate_helm_values(config)
            self.assertIn("performanceMode", v["vllmConfig"])

    def test_lora(self):
        config = ModelServiceConfig(
            model="llama3", enable_lora=True,
            lora_adapters=["finance", "code"],
        )
        v = generate_helm_values(config)
        self.assertTrue(v["vllmConfig"]["enableLora"])
        self.assertEqual(v["vllmConfig"]["maxLoras"], 2)

    def test_multi_gpu(self):
        config = ModelServiceConfig(model="llama3-70b", tensor_parallel=4)
        v = generate_helm_values(config)
        self.assertTrue(v["leaderWorkerSet"]["enabled"])
        self.assertEqual(v["leaderWorkerSet"]["size"], 4)

    def test_no_lws_single_gpu(self):
        config = ModelServiceConfig(model="llama3", tensor_parallel=1)
        v = generate_helm_values(config)
        self.assertNotIn("leaderWorkerSet", v)

    def test_autoscaling(self):
        config = ModelServiceConfig(model="llama3", replicas=2)
        v = generate_helm_values(config)
        self.assertTrue(v["autoscaling"]["enabled"])
        self.assertEqual(v["autoscaling"]["minReplicas"], 2)
        self.assertEqual(v["autoscaling"]["maxReplicas"], 8)

    def test_yaml_output(self):
        config = ModelServiceConfig(model="llama3")
        v = generate_helm_values(config)
        yaml = values_to_yaml(v)
        self.assertIn("modelService:", yaml)
        self.assertIn("vllmConfig:", yaml)
        self.assertIn("inferencePool:", yaml)

    def test_cli_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["deploy", "modelservice", "llama3",
                            "--preset", "throughput", "--tp", "4"])
        self.assertEqual(args.model, "llama3")
        self.assertEqual(args.preset, "throughput")
        self.assertEqual(args.tp, 4)

    def test_all_presets_exist(self):
        self.assertIn("balanced", PRESETS)
        self.assertIn("latency", PRESETS)
        self.assertIn("throughput", PRESETS)


class TestModelDB(unittest.TestCase):
    def test_model_count(self):
        from aictl.runtime.recommend import MODELS
        self.assertGreaterEqual(len(MODELS), 29)

    def test_deepseek_v4_in_db(self):
        from aictl.runtime.recommend import MODELS
        names = [m.name for m in MODELS]
        self.assertTrue(any("deepseek-v4" in n or "DeepSeek-V4" in n for n in names))

    def test_1m_context_model(self):
        from aictl.runtime.recommend import MODELS
        long_ctx = [m for m in MODELS if m.context_length >= 1_000_000]
        self.assertGreater(len(long_ctx), 0, "Should have 1M+ context models")

    def test_all_runtimes_represented(self):
        from aictl.runtime.recommend import MODELS
        runtimes = set(m.runtime for m in MODELS)
        self.assertIn("ollama", runtimes)
        self.assertIn("vllm", runtimes)

    def test_all_use_cases_represented(self):
        from aictl.runtime.recommend import MODELS
        cases = set(m.use_case for m in MODELS)
        for uc in ["chat", "code", "embedding", "vision", "stt"]:
            self.assertIn(uc, cases, f"Missing use_case: {uc}")


if __name__ == "__main__":
    unittest.main()
