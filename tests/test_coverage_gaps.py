"""Tests for previously uncovered modules: constants, selftest, orchestrator."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestConstants(unittest.TestCase):
    """Verify constants module has all required values."""

    def test_version(self):
        from aictl.core.constants import AICTL_VERSION
        self.assertRegex(AICTL_VERSION, r'^\d+\.\d+\.\d+$')

    def test_ports(self):
        from aictl.core.constants import (
            DAEMON_PORT, PROXY_PORT, MOCK_ENGINE_PORT,
            VLLM_DEFAULT_PORT, SGLANG_DEFAULT_PORT, OLLAMA_DEFAULT_PORT,
        )
        self.assertEqual(DAEMON_PORT, 7700)
        self.assertEqual(PROXY_PORT, 8080)
        self.assertEqual(MOCK_ENGINE_PORT, 9999)
        self.assertEqual(VLLM_DEFAULT_PORT, 8000)
        self.assertEqual(SGLANG_DEFAULT_PORT, 30000)
        self.assertEqual(OLLAMA_DEFAULT_PORT, 11434)

    def test_images(self):
        from aictl.core.constants import VLLM_IMAGE, SGLANG_IMAGE, OLLAMA_IMAGE
        self.assertIn("vllm", VLLM_IMAGE)
        self.assertIn("sglang", SGLANG_IMAGE)
        self.assertIn("ollama", OLLAMA_IMAGE)

    def test_timeouts(self):
        from aictl.core.constants import (
            ENGINE_HEALTH_TIMEOUT, PROXY_UPSTREAM_TIMEOUT,
        )
        self.assertGreater(ENGINE_HEALTH_TIMEOUT, 0)
        self.assertGreater(PROXY_UPSTREAM_TIMEOUT, 0)

    def test_slo_defaults(self):
        from aictl.core.constants import SLO_TTFT_MS, SLO_TPS
        self.assertEqual(SLO_TTFT_MS, 500)
        self.assertGreater(SLO_TPS, 0)

    def test_security(self):
        from aictl.core.constants import API_KEY_PREFIX, API_KEY_LENGTH
        self.assertEqual(API_KEY_PREFIX, "aios-")
        self.assertGreater(API_KEY_LENGTH, 16)

    def test_metering_prices(self):
        from aictl.core.constants import PRICE_PER_MILLION_INPUT, PRICE_PER_MILLION_OUTPUT
        self.assertGreater(PRICE_PER_MILLION_INPUT, 0)
        self.assertGreater(PRICE_PER_MILLION_OUTPUT, 0)

    def test_model_defaults(self):
        from aictl.core.constants import DEFAULT_MAX_MODEL_LEN, DEFAULT_GPU_MEMORY_UTIL
        self.assertEqual(DEFAULT_MAX_MODEL_LEN, 32768)
        self.assertAlmostEqual(DEFAULT_GPU_MEMORY_UTIL, 0.9)


class TestSelftest(unittest.TestCase):
    """Verify selftest command registers properly."""

    def test_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["selftest"])
        self.assertEqual(args.command, "selftest")


class TestOrchestrator(unittest.TestCase):
    """Verify orchestrator module imports and basic functionality."""

    def test_import(self):
        from aictl.stack.orchestrator import apply_stack, stop_stack, list_running
        self.assertTrue(callable(apply_stack))
        self.assertTrue(callable(stop_stack))
        self.assertTrue(callable(list_running))

    def test_list_running_empty(self):
        from aictl.stack.orchestrator import list_running
        result = list_running("nonexistent-stack-xyz")
        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
