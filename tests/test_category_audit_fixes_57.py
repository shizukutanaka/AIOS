"""Pass 57 regression tests: lora rank validation, max-loras cap, dynamo max_gpus, speculative validation."""

import unittest
from pathlib import Path
import tempfile


class TestLoraRankValidation(unittest.TestCase):
    """register_adapter must reject rank <= 0."""

    def _make_manager(self):
        from aictl.runtime.lora import LoRAManager
        tmpdir = tempfile.mkdtemp()
        return LoRAManager(Path(tmpdir))

    def test_register_rank_zero_raises(self):
        from aictl.runtime.lora import LoRAAdapter
        mgr = self._make_manager()
        adapter = LoRAAdapter(name="bad", base_model="llama", rank=0)
        with self.assertRaises(ValueError):
            mgr.register_adapter(adapter)

    def test_register_negative_rank_raises(self):
        from aictl.runtime.lora import LoRAAdapter
        mgr = self._make_manager()
        adapter = LoRAAdapter(name="bad", base_model="llama", rank=-4)
        with self.assertRaises(ValueError):
            mgr.register_adapter(adapter)

    def test_register_positive_rank_ok(self):
        from aictl.runtime.lora import LoRAAdapter
        mgr = self._make_manager()
        adapter = LoRAAdapter(name="good", base_model="llama", rank=16)
        mgr.register_adapter(adapter)  # Should not raise
        adapters = mgr.list_adapters("llama")
        self.assertEqual(len(adapters), 1)
        self.assertEqual(adapters[0].rank, 16)


class TestLoraMaxLorasCap(unittest.TestCase):
    """generate_vllm_args must cap --max-loras at MAX_LORA_ADAPTERS."""

    def _make_manager_with_adapters(self, count: int):
        from aictl.runtime.lora import LoRAAdapter, LoRAManager
        tmpdir = tempfile.mkdtemp()
        mgr = LoRAManager(Path(tmpdir))
        for i in range(count):
            adapter = LoRAAdapter(name=f"adapter{i}", base_model="llama",
                                  rank=16, path=f"/path/{i}", active=True)
            mgr.register_adapter(adapter)
        return mgr

    def test_args_cap_at_64_when_more_adapters(self):
        from aictl.core.constants import MAX_LORA_ADAPTERS
        mgr = self._make_manager_with_adapters(MAX_LORA_ADAPTERS + 10)
        args = mgr.generate_vllm_args("llama")
        max_loras_arg = next((a for a in args if a.startswith("--max-loras=")), None)
        self.assertIsNotNone(max_loras_arg)
        n = int(max_loras_arg.split("=")[1])
        self.assertLessEqual(n, MAX_LORA_ADAPTERS)

    def test_args_exact_count_when_under_limit(self):
        mgr = self._make_manager_with_adapters(5)
        args = mgr.generate_vllm_args("llama")
        max_loras_arg = next((a for a in args if a.startswith("--max-loras=")), None)
        self.assertIsNotNone(max_loras_arg)
        n = int(max_loras_arg.split("=")[1])
        self.assertEqual(n, 5)

    def test_max_lora_adapters_constant_is_64(self):
        from aictl.core.constants import MAX_LORA_ADAPTERS
        self.assertEqual(MAX_LORA_ADAPTERS, 64)


class TestDynamoMaxGpusValidation(unittest.TestCase):
    """estimate_dgdr_resources must reject max_gpus <= 0."""

    def test_zero_max_gpus_raises(self):
        from aictl.runtime.dynamo import estimate_dgdr_resources, DGDRSpec
        spec = DGDRSpec(model="meta-llama/Meta-Llama-3-8B", max_gpus=0)
        with self.assertRaises(ValueError):
            estimate_dgdr_resources(spec)

    def test_negative_max_gpus_raises(self):
        from aictl.runtime.dynamo import estimate_dgdr_resources, DGDRSpec
        spec = DGDRSpec(model="meta-llama/Meta-Llama-3-8B", max_gpus=-1)
        with self.assertRaises(ValueError):
            estimate_dgdr_resources(spec)

    def test_positive_max_gpus_ok(self):
        from aictl.runtime.dynamo import estimate_dgdr_resources, DGDRSpec
        spec = DGDRSpec(model="meta-llama/Meta-Llama-3-8B", max_gpus=4)
        result = estimate_dgdr_resources(spec)
        self.assertIn("gpus_needed", result)
        self.assertGreater(result["gpus_needed"], 0)
        self.assertLessEqual(result["gpus_needed"], 4)


class TestSpeculativeValidation(unittest.TestCase):
    """generate_vllm_args and generate_sglang_args must validate inputs."""

    def test_vllm_zero_spec_tokens_raises(self):
        from aictl.runtime.speculative import SpeculativeConfig, generate_vllm_args
        config = SpeculativeConfig(method="ngram", num_speculative_tokens=0)
        with self.assertRaises(ValueError):
            generate_vllm_args(config)

    def test_vllm_negative_spec_tokens_raises(self):
        from aictl.runtime.speculative import SpeculativeConfig, generate_vllm_args
        config = SpeculativeConfig(method="ngram", num_speculative_tokens=-3)
        with self.assertRaises(ValueError):
            generate_vllm_args(config)

    def test_vllm_eagle3_no_draft_model_raises(self):
        from aictl.runtime.speculative import SpeculativeConfig, generate_vllm_args
        config = SpeculativeConfig(method="eagle3", draft_model="", num_speculative_tokens=3)
        with self.assertRaises(ValueError):
            generate_vllm_args(config)

    def test_vllm_eagle3_with_draft_model_ok(self):
        from aictl.runtime.speculative import SpeculativeConfig, generate_vllm_args
        config = SpeculativeConfig(
            method="eagle3",
            draft_model="yuhuili/EAGLE3-LLaMA3.1-Instruct-8B",
            num_speculative_tokens=3,
        )
        args = generate_vllm_args(config)
        self.assertTrue(any("speculative-config" in a for a in args))

    def test_sglang_zero_spec_tokens_raises(self):
        from aictl.runtime.speculative import SpeculativeConfig, generate_sglang_args
        config = SpeculativeConfig(method="ngram", num_speculative_tokens=0)
        with self.assertRaises(ValueError):
            generate_sglang_args(config)

    def test_sglang_eagle3_no_draft_model_raises(self):
        from aictl.runtime.speculative import SpeculativeConfig, generate_sglang_args
        config = SpeculativeConfig(method="eagle3", draft_model="", num_speculative_tokens=3)
        with self.assertRaises(ValueError):
            generate_sglang_args(config)

    def test_none_method_always_returns_empty(self):
        from aictl.runtime.speculative import SpeculativeConfig, generate_vllm_args, generate_sglang_args
        config = SpeculativeConfig(method="none", num_speculative_tokens=0)
        self.assertEqual(generate_vllm_args(config), [])
        self.assertEqual(generate_sglang_args(config), [])


if __name__ == "__main__":
    unittest.main()
