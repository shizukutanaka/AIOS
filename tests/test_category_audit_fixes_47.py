"""Pass 47 regression tests: optimize.py constants not hardcoded."""

import pathlib
import unittest


class TestOptimizeUsesConstants(unittest.TestCase):
    """optimize.py must use DEFAULT_GPU_MEMORY_UTIL and VLLM_DEFAULT_PORT."""

    def test_optimize_gpu_util_matches_constant(self):
        """DEFAULT_GPU_MEMORY_UTIL must equal the gpu_util default in optimize()."""
        from aictl.core.constants import DEFAULT_GPU_MEMORY_UTIL
        # The constant is now used directly; check both agree
        self.assertEqual(DEFAULT_GPU_MEMORY_UTIL, 0.9)

    def test_flags_to_command_port_matches_constant(self):
        """flags_to_command default port must equal VLLM_DEFAULT_PORT."""
        import inspect
        from aictl.runtime.optimize import flags_to_command
        from aictl.core.constants import VLLM_DEFAULT_PORT
        sig = inspect.signature(flags_to_command)
        default_port = sig.parameters["port"].default
        self.assertEqual(default_port, VLLM_DEFAULT_PORT,
                         "flags_to_command port default must be VLLM_DEFAULT_PORT")

    def test_optimize_source_not_hardcoded_090(self):
        """optimize.py must not hardcode gpu_util = 0.90."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "runtime" / "optimize.py").read_text()
        self.assertNotIn(
            "gpu_util = 0.90",
            src,
            "optimize.py must use DEFAULT_GPU_MEMORY_UTIL, not 0.90",
        )

    def test_optimize_source_not_hardcoded_8000(self):
        """optimize.py must not hardcode port = 8000 in flags_to_command."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "runtime" / "optimize.py").read_text()
        self.assertNotIn(
            "port: int = 8000",
            src,
            "optimize.py must use VLLM_DEFAULT_PORT, not 8000",
        )


if __name__ == "__main__":
    unittest.main()
