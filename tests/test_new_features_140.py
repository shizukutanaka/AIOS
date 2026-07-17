"""Pass 140: LLMISvcConfig must validate physical quantities (KServe parity).

After Pass 139 gave ModelServiceConfig a __post_init__, the third K8s manifest
generator — KServe's LLMISvcConfig — was the last sibling without one. An SDK
caller (`stack_to_llmisvc` + `LLMISvcConfig` are both public) constructing a
config with a negative field emitted an invalid LLMInferenceService:

    cfg = LLMISvcConfig(replicas=-2)
    stack_to_llmisvc(get_recipe("code-assist"), cfg)
      -> {"kind": "LLMInferenceService", "spec": {"replicas": -2, ...}}

a manifest that looks valid but is rejected at apply time. Same V1 class as
disagg/modelservice; the Python SDK is a documented surface, so this is a real
bug for SDK users (it is not reachable from `cluster export`, which only sets
performance_mode/prefix-caching and leaves the counts at their defaults).

Fix: LLMISvcConfig.__post_init__ rejects negative replicas, sub-1 tensor/pipeline
parallelism, negative max_model_len/speculative_tokens, and out-of-range
gpu_memory_utilization — guarding every SDK construction path.
"""

from __future__ import annotations

import json
import unittest


def _cfg(**kw):
    from aictl.stack.kserve import LLMISvcConfig
    return LLMISvcConfig(**kw)


class TestLLMISvcConfigValidation(unittest.TestCase):
    def test_negative_replicas_rejected(self):
        with self.assertRaises(ValueError):
            _cfg(replicas=-2)

    def test_zero_replicas_allowed(self):
        self.assertEqual(_cfg(replicas=0).replicas, 0)

    def test_zero_tensor_parallel_rejected(self):
        with self.assertRaises(ValueError):
            _cfg(tensor_parallel=0)

    def test_negative_pipeline_parallel_rejected(self):
        with self.assertRaises(ValueError):
            _cfg(pipeline_parallel=-1)

    def test_negative_max_model_len_rejected(self):
        with self.assertRaises(ValueError):
            _cfg(max_model_len=-5)

    def test_zero_max_model_len_allowed_auto(self):
        # 0 = "auto/unset" is the documented default here.
        self.assertEqual(_cfg(max_model_len=0).max_model_len, 0)

    def test_negative_speculative_tokens_rejected(self):
        with self.assertRaises(ValueError):
            _cfg(speculative_tokens=-3)

    def test_gpu_mem_util_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            _cfg(gpu_memory_utilization=1.5)
        with self.assertRaises(ValueError):
            _cfg(gpu_memory_utilization=0.0)

    def test_valid_config_accepted(self):
        c = _cfg(replicas=3, tensor_parallel=4, pipeline_parallel=2)
        self.assertEqual((c.replicas, c.tensor_parallel, c.pipeline_parallel), (3, 4, 2))


class TestGeneratedManifestNeverNegative(unittest.TestCase):
    def test_bad_config_cannot_reach_generator(self):
        # You can no longer construct a config that would emit replicas: -2.
        with self.assertRaises(ValueError):
            _cfg(replicas=-2)

    def test_valid_config_emits_valid_llmisvc(self):
        from aictl.stack.kserve import stack_to_llmisvc
        from aictl.stack.manifest import get_recipe
        m = get_recipe("code-assist")
        res = stack_to_llmisvc(m, _cfg(replicas=2, tensor_parallel=2))
        svc = [r for r in res if r.get("kind") == "LLMInferenceService"]
        self.assertTrue(svc)
        for r in svc:
            self.assertGreaterEqual(r["spec"]["replicas"], 0)


if __name__ == "__main__":
    unittest.main()
