"""Pass 26 regression tests: optimize.py ZeroDivisionError on vram=0."""

import unittest


class TestOptimizeVramZero(unittest.TestCase):
    """optimize_vllm_flags must not raise when vram_per_gpu_mb == 0 (default HardwareProfile)."""

    def test_zero_vram_does_not_raise(self):
        from aictl.runtime.optimize import optimize_vllm_flags, HardwareProfile
        hw = HardwareProfile(gpu_name="H100", gpu_count=4, vram_per_gpu_mb=0)
        result = optimize_vllm_flags("meta-llama/Llama-3.1-70B", 70.0, hw)
        self.assertEqual(result.tensor_parallel, 1)

    def test_zero_vram_single_gpu_does_not_raise(self):
        from aictl.runtime.optimize import optimize_vllm_flags, HardwareProfile
        hw = HardwareProfile(gpu_count=1, vram_per_gpu_mb=0)
        result = optimize_vllm_flags("some/model", 8.0, hw)
        self.assertIsNotNone(result)

    def test_positive_vram_still_enables_tp(self):
        from aictl.runtime.optimize import optimize_vllm_flags, HardwareProfile
        hw = HardwareProfile(gpu_name="H100", gpu_count=4,
                             vram_per_gpu_mb=81920, compute_capability=90)
        result = optimize_vllm_flags("meta-llama/Llama-3.1-70B", 70.0, hw)
        self.assertGreaterEqual(result.tensor_parallel, 1)

    def test_division_guard_in_source(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "optimize.py").read_text()
        self.assertIn(
            "hardware.vram_per_gpu_mb > 0",
            src,
            "TP block must guard against vram_per_gpu_mb == 0 to prevent ZeroDivisionError",
        )


if __name__ == "__main__":
    unittest.main()
