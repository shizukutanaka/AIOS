"""Tests for vLLM optimization flag generator."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.optimize import (
    optimize_vllm_flags, flags_to_command,
    HardwareProfile, OptimizeResult, GPU_CC,
)


class TestOptimize(unittest.TestCase):
    def _h100(self, **kw):
        return HardwareProfile(gpu_name="H100", gpu_count=1,
                               vram_per_gpu_mb=81920, compute_capability=90, **kw)

    def _b200(self, **kw):
        return HardwareProfile(gpu_name="B200", gpu_count=1,
                               vram_per_gpu_mb=196608, compute_capability=100, **kw)

    def test_basic_flags(self):
        r = optimize_vllm_flags("llama3", 8.0, self._h100())
        self.assertIn("--model", r.flags)
        self.assertIn("llama3", r.flags)
        self.assertIn("--v1", r.flags)

    def test_fp8_kv_cache_on_hopper(self):
        r = optimize_vllm_flags("llama3", 8.0, self._h100())
        self.assertEqual(r.kv_cache_dtype, "fp8")
        self.assertIn("--kv-cache-dtype=fp8", r.flags)

    def test_fp8_weights_on_blackwell(self):
        r = optimize_vllm_flags("llama3", 8.0, self._b200())
        self.assertIn("--dtype=fp8", r.flags)

    def test_prefix_caching_enabled(self):
        r = optimize_vllm_flags("llama3", 8.0, self._h100())
        self.assertIn("--enable-prefix-caching", r.flags)

    def test_chunked_prefill_enabled(self):
        r = optimize_vllm_flags("llama3", 8.0, self._h100())
        self.assertIn("--enable-chunked-prefill", r.flags)

    def test_tp_for_large_model(self):
        hw = HardwareProfile(gpu_name="H100", gpu_count=8,
                             vram_per_gpu_mb=81920, compute_capability=90)
        r = optimize_vllm_flags("llama405b", 405.0, hw)
        self.assertGreater(r.tensor_parallel, 1)
        tp_flag = [f for f in r.flags if "tensor-parallel" in f]
        self.assertEqual(len(tp_flag), 1)

    def test_no_tp_for_small_model(self):
        r = optimize_vllm_flags("llama3-8b", 8.0, self._h100())
        self.assertEqual(r.tensor_parallel, 1)
        tp_flags = [f for f in r.flags if "tensor-parallel" in f]
        self.assertEqual(len(tp_flags), 0)

    def test_performance_mode(self):
        r = optimize_vllm_flags("llama3", 8.0, self._h100(), objective="throughput")
        self.assertEqual(r.performance_mode, "throughput")
        self.assertIn("--performance-mode=throughput", r.flags)

    def test_throughput_estimate(self):
        r = optimize_vllm_flags("llama3", 8.0, self._h100())
        self.assertGreater(r.estimated_throughput_tps, 0)

    def test_b200_higher_throughput(self):
        r_h100 = optimize_vllm_flags("llama3", 8.0, self._h100())
        r_b200 = optimize_vllm_flags("llama3", 8.0, self._b200())
        self.assertGreater(r_b200.estimated_throughput_tps, r_h100.estimated_throughput_tps)

    def test_flags_to_command(self):
        r = optimize_vllm_flags("llama3", 8.0, self._h100())
        cmd = flags_to_command(r)
        self.assertTrue(cmd.startswith("vllm serve"))
        self.assertIn("--model", cmd)

    def test_old_gpu_fp8_e5m2(self):
        hw = HardwareProfile(gpu_name="RTX 3090", gpu_count=1,
                             vram_per_gpu_mb=24576, compute_capability=86)
        r = optimize_vllm_flags("llama3", 8.0, hw)
        # CC 86 supports fp8_e5m2 (but not full fp8)
        self.assertEqual(r.kv_cache_dtype, "fp8_e5m2")

    def test_cli_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["deploy", "optimize", "llama3",
                            "--gpu", "H100", "--objective", "throughput"])
        self.assertEqual(args.model, "llama3")
        self.assertEqual(args.gpu, "H100")
        self.assertEqual(args.objective, "throughput")

    def test_gpu_cc_map(self):
        self.assertEqual(GPU_CC["B200"], 100)
        self.assertEqual(GPU_CC["H100"], 90)
        self.assertGreater(GPU_CC["RTX 5090"], GPU_CC["RTX 4090"])


if __name__ == "__main__":
    unittest.main()
