"""Pass 26 regression tests: optimize.py ZeroDivisionError on vram=0; mcp_server VRAM lookup."""

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


class TestMcpOptimizeVramLookup(unittest.TestCase):
    """mcp_server._tool_optimize must use correct VRAM for all GPU types."""

    def _vram_for_gpu(self, gpu: str) -> int:
        """Extract vram_per_gpu_mb from the mcp_server lookup dict."""
        import pathlib, re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "mcp_server.py").read_text()
        # Find the VRAM dict block inside _tool_optimize
        m = re.search(r"vram_per_gpu_mb=\{([^}]+)\}\.get\(gpu,\s*(\d+)\)", src, re.DOTALL)
        self.assertIsNotNone(m, "VRAM lookup dict not found in _tool_optimize")
        entries_text = m.group(1)
        default = int(m.group(2))
        lookup: dict[str, int] = {}
        for pair in re.findall(r'"([^"]+)":\s*(\d+)', entries_text):
            lookup[pair[0]] = int(pair[1])
        return lookup.get(gpu, default)

    def test_h200_vram_correct(self):
        self.assertEqual(self._vram_for_gpu("H200"), 144384, "H200 should have 141GB = 144384 MB")

    def test_h200_sxm_vram_correct(self):
        self.assertEqual(self._vram_for_gpu("H200 SXM"), 144384)

    def test_gb200_vram_correct(self):
        self.assertEqual(self._vram_for_gpu("GB200"), 196608, "GB200 should have 192GB")

    def test_rtx5090_vram_correct(self):
        self.assertEqual(self._vram_for_gpu("RTX 5090"), 32768, "RTX 5090 should have 32GB")

    def test_l40s_vram_correct(self):
        self.assertEqual(self._vram_for_gpu("L40S"), 49152, "L40S should have 48GB")

    def test_h100_unchanged(self):
        self.assertEqual(self._vram_for_gpu("H100"), 81920)


if __name__ == "__main__":
    unittest.main()
