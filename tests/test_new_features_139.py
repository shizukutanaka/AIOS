"""Pass 139: ModelServiceConfig must validate physical quantities (>= 1).

`aictl deploy disagg` validates its replica counts in DisaggConfig.__post_init__
(raising ValueError -> "Invalid input"), but the sibling `deploy modelservice`
had NO such guard. A negative/zero flag flowed straight into the generated
llm-d Helm values:

    aictl deploy modelservice llama --replicas -2 --tp -1
      -> replicaCount: -2
         tensorParallelSize: -1

a manifest that looks valid but is rejected cluster-side at `helm install` time
— a confusing error far from the aictl invocation that produced it. Same V1
"physical quantity >= 1" invariant.

Fix: ModelServiceConfig gains a __post_init__ (mirroring DisaggConfig) that
rejects replicas/gpu_count/tensor_parallel/max_model_len < 1. This guards both
the CLI (ValueError -> the standard "Invalid input" handler) and SDK callers
that build the config directly.
"""

from __future__ import annotations

import unittest


def _cfg(**kw):
    from aictl.stack.modelservice import ModelServiceConfig
    base = dict(model="meta-llama/Llama-3-8B")
    base.update(kw)
    return ModelServiceConfig(**base)


class TestModelServiceConfigValidation(unittest.TestCase):
    def test_negative_replicas_rejected(self):
        with self.assertRaises(ValueError):
            _cfg(replicas=-2)

    def test_zero_replicas_allowed_scale_to_zero(self):
        # replicas=0 is a legitimate scale-to-zero baseline (HPA block handles it).
        self.assertEqual(_cfg(replicas=0).replicas, 0)

    def test_negative_tensor_parallel_rejected(self):
        with self.assertRaises(ValueError):
            _cfg(tensor_parallel=-1)

    def test_zero_tensor_parallel_rejected(self):
        # tensor_parallel is a divisor/group size — meaningless below 1.
        with self.assertRaises(ValueError):
            _cfg(tensor_parallel=0)

    def test_negative_gpu_count_rejected(self):
        with self.assertRaises(ValueError):
            _cfg(gpu_count=-1)

    def test_zero_max_model_len_rejected(self):
        with self.assertRaises(ValueError):
            _cfg(max_model_len=0)

    def test_valid_config_accepted(self):
        c = _cfg(replicas=3, tensor_parallel=2, gpu_count=2)
        self.assertEqual(c.replicas, 3)
        self.assertEqual(c.tensor_parallel, 2)

    def test_defaults_valid(self):
        # The default config (all 1s) must construct cleanly.
        c = _cfg()
        self.assertEqual(c.replicas, 1)


class TestGeneratedManifestNeverNegative(unittest.TestCase):
    def test_helm_values_replica_count_positive(self):
        from aictl.stack.modelservice import generate_helm_values
        values = generate_helm_values(_cfg(replicas=2, tensor_parallel=2))
        self.assertGreaterEqual(values["servingEngine"]["replicaCount"], 1)
        self.assertGreaterEqual(values["vllmConfig"]["tensorParallelSize"], 1)

    def test_bad_config_cannot_reach_generate(self):
        # The whole point: you can't even construct a config that would emit a
        # negative replicaCount, so generate_helm_values never sees one.
        with self.assertRaises(ValueError):
            _cfg(replicas=-5)


if __name__ == "__main__":
    unittest.main()
