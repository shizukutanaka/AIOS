"""Pass 88 (loop): DGDR throughput estimate must not make bigger models faster.

Functional logic bug found inspecting real output: `deploy plan` reported an 8B
model at 280 tps but a 70B at 560 tps — bigger = faster. estimate_dgdr_resources
computed est_tps = per_gpu_tps * gpus_needed, and gpus_needed grows with VRAM, so
a larger model that needs more GPUs appeared faster. Decode is bandwidth-bound, so
throughput is now scaled inversely with model size (per-GPU figures calibrated for
an ~8B reference), guaranteeing monotonic decrease on equal hardware.
"""

from __future__ import annotations

import unittest


def _tps(model: str, hardware: str = "H100", quant: str = "auto") -> int:
    from aictl.runtime.dynamo import estimate_dgdr_resources, DGDRSpec
    return estimate_dgdr_resources(
        DGDRSpec(model=model, hardware=hardware, quantization=quant))["estimated_tps"]


class TestDgdrThroughputMonotonic(unittest.TestCase):

    def test_larger_model_not_faster(self):
        # Strictly decreasing throughput as model size grows on equal hardware.
        t8 = _tps("meta-llama/Llama-3.1-8B")
        t14 = _tps("qwen2.5:14b")
        t27 = _tps("gemma:27b")
        t70 = _tps("meta-llama/Llama-3.1-70B")
        self.assertGreater(t8, t14)
        self.assertGreater(t14, t27)
        self.assertGreater(t27, t70)

    def test_8b_h100_reference_value(self):
        # 8B is the reference size, so it gets the full per-GPU H100 figure.
        self.assertEqual(_tps("meta-llama/Llama-3.1-8B"), 280)

    def test_70b_below_default_sla(self):
        # A 70B single-replica should not claim to meet a 100 tps default SLA.
        from aictl.runtime.dynamo import estimate_dgdr_resources, DGDRSpec
        est = estimate_dgdr_resources(DGDRSpec(model="meta-llama/Llama-3.1-70B",
                                               hardware="H100"))
        self.assertFalse(est["meets_sla"])

    def test_tps_always_positive(self):
        # Even a huge model must report a positive (clamped) throughput.
        self.assertGreaterEqual(_tps("llama-405b"), 1)


if __name__ == "__main__":
    unittest.main()
