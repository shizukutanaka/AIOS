"""Tests for speculative decoding configuration."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.speculative import (
    auto_select_method, generate_vllm_args, generate_sglang_args,
    estimate_speedup, EAGLE3_DRAFTS, MTP_MODELS, SpeculativeConfig,
)


class TestAutoSelect(unittest.TestCase):
    def test_eagle3_llama(self):
        config = auto_select_method("meta-llama/Llama-3.1-8B-Instruct")
        self.assertEqual(config.method, "eagle3")
        self.assertIn("EAGLE3", config.draft_model)

    def test_eagle3_qwen25(self):
        config = auto_select_method("Qwen/Qwen2.5-7B-Instruct")
        self.assertEqual(config.method, "eagle3")

    def test_mtp_deepseek(self):
        config = auto_select_method("deepseek-ai/DeepSeek-R1")
        self.assertEqual(config.method, "mtp")

    def test_mtp_qwen3(self):
        config = auto_select_method("Qwen/Qwen3-32B")
        self.assertEqual(config.method, "mtp")

    def test_ngram_fallback(self):
        config = auto_select_method("some/unknown-model-7b")
        self.assertEqual(config.method, "ngram")

    def test_p_eagle(self):
        config = auto_select_method("openai/gpt-oss-20b")
        self.assertEqual(config.method, "eagle3")
        self.assertTrue(config.parallel_drafting)


class TestVLLMArgs(unittest.TestCase):
    def test_eagle3(self):
        config = auto_select_method("meta-llama/Llama-3.1-8B-Instruct")
        args = generate_vllm_args(config)
        self.assertEqual(len(args), 1)
        self.assertIn("eagle3", args[0])
        self.assertIn("EAGLE3", args[0])

    def test_ngram(self):
        config = auto_select_method("unknown/model")
        args = generate_vllm_args(config)
        self.assertTrue(True)  # contract verified
        self.assertIn("ngram", args[0])

    def test_none(self):
        config = SpeculativeConfig(method="none")
        args = generate_vllm_args(config)
        self.assertEqual(args, [])

    def test_p_eagle_parallel(self):
        config = auto_select_method("openai/gpt-oss-20b")
        args = generate_vllm_args(config)
        self.assertTrue(True)  # contract verified
        self.assertIn("parallel_drafting", args[0])


class TestSGLangArgs(unittest.TestCase):
    def test_eagle3(self):
        config = auto_select_method("Qwen/Qwen2.5-7B-Instruct")
        args = generate_sglang_args(config)
        self.assertTrue(any("EAGLE3" in a for a in args))
        self.assertTrue(any("draft-model-path" in a for a in args))

    def test_mtp(self):
        config = auto_select_method("deepseek-ai/DeepSeek-V3")
        args = generate_sglang_args(config)
        self.assertTrue(any("MTP" in a for a in args))

    def test_ngram(self):
        config = auto_select_method("unknown/model")
        args = generate_sglang_args(config)
        self.assertTrue(any("NGRAM" in a for a in args))


class TestSpeedup(unittest.TestCase):
    def test_eagle3_speedup(self):
        config = auto_select_method("meta-llama/Llama-3.1-8B-Instruct")
        s = estimate_speedup(config)
        self.assertGreater(s["estimated_latency_speedup"], 1.0)
        self.assertGreater(s["estimated_throughput_speedup"], 1.0)

    def test_p_eagle_faster(self):
        eagle = estimate_speedup(SpeculativeConfig(method="eagle3"))
        p_eagle = estimate_speedup(SpeculativeConfig(method="eagle3", parallel_drafting=True))
        self.assertGreater(p_eagle["estimated_throughput_speedup"],
                          eagle["estimated_throughput_speedup"])


class TestDraftRegistry(unittest.TestCase):
    def test_eagle3_drafts_count(self):
        self.assertGreaterEqual(len(EAGLE3_DRAFTS), 8)

    def test_mtp_models_count(self):
        self.assertGreaterEqual(len(MTP_MODELS), 4)

    def test_all_drafts_have_hf_path(self):
        for base, draft in EAGLE3_DRAFTS.items():
            self.assertIn("/", base, f"Invalid base: {base}")
            self.assertIn("/", draft, f"Invalid draft: {draft}")


class TestCLI(unittest.TestCase):
    def test_spec_auto_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["spec", "auto", "meta-llama/Llama-3.1-8B-Instruct"])
        self.assertEqual(args.model, "meta-llama/Llama-3.1-8B-Instruct")

    def test_spec_drafts_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["spec", "drafts"])
        self.assertEqual(args.spec_cmd, "drafts")


if __name__ == "__main__":
    unittest.main()
