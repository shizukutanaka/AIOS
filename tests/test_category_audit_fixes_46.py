"""Pass 46 regression tests: config.py and mock_engine.py use constants, not literals."""

import pathlib
import unittest


class TestConfigUsesConstants(unittest.TestCase):
    """EngineEndpoints and DaemonConfig must derive defaults from constants.py."""

    def test_engine_endpoints_vllm_matches_constant(self):
        """EngineEndpoints().vllm must equal VLLM_DEFAULT_URL constant."""
        from aictl.core.config import EngineEndpoints
        from aictl.core.constants import VLLM_DEFAULT_URL
        self.assertEqual(EngineEndpoints().vllm, VLLM_DEFAULT_URL)

    def test_engine_endpoints_ollama_matches_constant(self):
        """EngineEndpoints().ollama must equal OLLAMA_DEFAULT_URL constant."""
        from aictl.core.config import EngineEndpoints
        from aictl.core.constants import OLLAMA_DEFAULT_URL
        self.assertEqual(EngineEndpoints().ollama, OLLAMA_DEFAULT_URL)

    def test_engine_endpoints_sglang_matches_constant(self):
        """EngineEndpoints().sglang must equal SGLANG_DEFAULT_URL constant."""
        from aictl.core.config import EngineEndpoints
        from aictl.core.constants import SGLANG_DEFAULT_URL
        self.assertEqual(EngineEndpoints().sglang, SGLANG_DEFAULT_URL)

    def test_daemon_config_port_matches_constant(self):
        """DaemonConfig().port must equal DAEMON_PORT constant."""
        from aictl.core.config import DaemonConfig
        from aictl.core.constants import DAEMON_PORT
        self.assertEqual(DaemonConfig().port, DAEMON_PORT)

    def test_config_source_not_hardcoded_8000(self):
        """config.py must not hardcode '8000' as vllm default."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "core" / "config.py").read_text()
        self.assertNotIn(
            '"http://localhost:8000"',
            src,
            "config.py must use VLLM_DEFAULT_URL constant, not hardcoded URL",
        )

    def test_config_source_not_hardcoded_7700(self):
        """config.py must not hardcode 7700 as daemon port default."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "core" / "config.py").read_text()
        self.assertNotIn(
            "port: int = 7700",
            src,
            "config.py must use DAEMON_PORT constant, not hardcoded 7700",
        )


class TestMockEngineVersionConstant(unittest.TestCase):
    """mock_engine.py default response must use AICTL_VERSION, not a hardcoded string."""

    def test_default_response_uses_aictl_version(self):
        """RESPONSES['default'] must contain the current AICTL_VERSION."""
        from aictl.daemon.mock_engine import RESPONSES
        from aictl.core.constants import AICTL_VERSION
        self.assertIn(
            AICTL_VERSION,
            RESPONSES["default"],
            "mock_engine default response must embed AICTL_VERSION, not a hardcoded version",
        )

    def test_mock_engine_source_not_hardcoded_version(self):
        """mock_engine.py must not hardcode 'v1.6.0' in RESPONSES dict."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "daemon" / "mock_engine.py").read_text()
        self.assertNotIn(
            '"v1.6.0"',
            src,
            "mock_engine.py must not hardcode 'v1.6.0' — use AICTL_VERSION",
        )


if __name__ == "__main__":
    unittest.main()
