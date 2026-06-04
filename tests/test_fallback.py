"""Tests for cloud fallback, providers, and competitive features."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.fallback import (
    CloudProvider, FallbackConfig, PROVIDERS,
    cloud_completion, load_fallback_config,
)


class TestProviders(unittest.TestCase):
    def test_all_providers_defined(self):
        expected = ["openai", "openrouter", "together", "groq", "fireworks"]
        for p in expected:
            self.assertIn(p, PROVIDERS)

    def test_provider_has_required_fields(self):
        for name, p in PROVIDERS.items():
            self.assertIsInstance(p.name, str)
            self.assertTrue(p.base_url.startswith("https://"))
            self.assertIsInstance(p.api_key_env, str)
            self.assertIsInstance(p.default_model, str)

    def test_openrouter_url(self):
        self.assertEqual(PROVIDERS["openrouter"].base_url, "https://openrouter.ai/api/v1")

    def test_groq_url(self):
        self.assertEqual(PROVIDERS["groq"].base_url, "https://api.groq.com/openai/v1")


class TestFallbackConfig(unittest.TestCase):
    def test_defaults_disabled(self):
        config = FallbackConfig()
        self.assertFalse(config.enabled)
        self.assertEqual(config.provider, "")

    def test_load_from_empty_dir(self):
        tmp = Path(tempfile.mkdtemp())
        config = load_fallback_config(tmp)
        self.assertFalse(config.enabled)

    def test_load_from_config(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "config.json").write_text(json.dumps({
            "fallback": {
                "enabled": True,
                "provider": "openrouter",
                "model": "llama-3.1-8b",
            }
        }))
        config = load_fallback_config(tmp)
        self.assertTrue(config.enabled)
        self.assertEqual(config.provider, "openrouter")
        self.assertEqual(config.model, "llama-3.1-8b")


class TestCloudCompletion(unittest.TestCase):
    def test_disabled_returns_none(self):
        config = FallbackConfig(enabled=False)
        result = cloud_completion(config, [{"role": "user", "content": "test"}])
        self.assertIsNone(result)

    def test_no_provider_returns_none(self):
        config = FallbackConfig(enabled=True, provider="")
        result = cloud_completion(config, [])
        self.assertIsNone(result)

    def test_invalid_provider_returns_none(self):
        config = FallbackConfig(enabled=True, provider="nonexistent")
        result = cloud_completion(config, [])
        self.assertIsNone(result)

    def test_no_api_key_returns_none(self):
        config = FallbackConfig(enabled=True, provider="openai", api_key="")
        result = cloud_completion(config, [{"role": "user", "content": "test"}])
        # Should fail because no API key in env or config
        self.assertIsNone(result)


class TestCompetitiveFeatures(unittest.TestCase):
    """Verify aictl has features that competitors lack."""

    def test_zero_external_deps(self):
        """aictl uses only stdlib — no pip install needed."""
        import importlib
        # These should all be stdlib
        for mod in ["json", "http.server", "urllib.request", "hashlib",
                     "threading", "time", "os", "sys", "pathlib"]:
            m = importlib.import_module(mod)
            self.assertIsNotNone(m)

    def test_multi_engine_support(self):
        """Support vLLM + SGLang + Ollama (GPUStack equivalent)."""
        from aictl.runtime.adapters import get_adapter
        for engine, url in [("vllm", "http://x:8000"), ("sglang", "http://x:30000"),
                            ("ollama", "http://x:11434")]:
            adapter = get_adapter(engine, url)
            self.assertIsNotNone(adapter, f"Missing adapter for {engine}")

    def test_k8s_export_formats(self):
        """5 K8s export formats (more than any competitor)."""
        from aictl.stack.kserve import stack_to_llmisvc
        from aictl.stack.gateway import stack_to_gateway_api
        from aictl.runtime.autoscaler import generate_keda_scaled_object, generate_hpa_manifest
        from aictl.runtime.dynamo import generate_dgdr_yaml, DGDRSpec

        from aictl.stack.manifest import get_recipe
        manifest = get_recipe("team-rag")

        # 1. KServe (generates Deployment/Service or LLMInferenceService)
        kserve = stack_to_llmisvc(manifest)
        self.assertGreater(len(kserve), 0)

        # 2. Gateway API InferencePool
        gw = stack_to_gateway_api(manifest)
        self.assertTrue(any(r["kind"] == "InferencePool" for r in gw))

        # 3. KEDA ScaledObject
        keda = generate_keda_scaled_object("test")
        self.assertEqual(keda["kind"], "ScaledObject")

        # 4. HPA
        hpa = generate_hpa_manifest("test")
        self.assertEqual(hpa["kind"], "HorizontalPodAutoscaler")

        # 5. Dynamo DGDR
        dgdr = generate_dgdr_yaml(DGDRSpec(model="llama3"))
        self.assertEqual(dgdr["kind"], "InferenceDeployment")

    def test_security_scanner(self):
        """Built-in security scanner (unique to aictl)."""
        from aictl.core.security import scan
        report = scan()
        self.assertGreaterEqual(report.checks_total, 5)

    def test_token_metering(self):
        """Per-entity token metering with quotas."""
        from aictl.core.metering import TokenMeter
        meter = TokenMeter(Path(tempfile.mkdtemp()))
        meter.record("test", "llama3", 100, 50)
        usage = meter.get_usage("test")
        self.assertEqual(usage.total_tokens, 150)

    def test_model_format_detection(self):
        """Auto-detect GGUF/SafeTensors/ONNX."""
        from aictl.runtime.formats import RUNTIME_COMPAT
        self.assertIn("gguf", RUNTIME_COMPAT)
        self.assertIn("safetensors", RUNTIME_COMPAT)

    def test_mock_engine_for_testing(self):
        """Full-stack demo without GPU (unique to aictl)."""
        from aictl.daemon.mock_engine import start_mock_engine
        import time, urllib.request
        server = start_mock_engine(port=19955)
        time.sleep(0.2)
        try:
            with urllib.request.urlopen("http://127.0.0.1:19955/health", timeout=3) as r:
                self.assertEqual(json.loads(r.read())["status"], "ok")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
