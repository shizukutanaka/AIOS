"""Pass 176 (IMPROVEMENTS.md item D): LMDeploy, TensorRT-LLM, LM Studio
engine adapters.

`runtime/adapters.py` detected only vLLM/SGLang/Ollama; the 2026 field also
treats LMDeploy (TurboMind), TensorRT-LLM (trtllm-serve), and LM Studio as
mainstream, all OpenAI-compatible. This adds adapters for all three,
following the exact shape of the existing VLLMAdapter/SGLangAdapter
(health via /health + /v1/models; LM Studio has no dedicated /health route
so /v1/models responding IS the readiness signal, matching how real local
LM Studio servers behave) and OllamaAdapter's honest scrape_metrics()
fallback (none of the three has a documented Prometheus contract, so
metrics return basic status rather than guessing at metric names).

Critical correctness point verified here: EngineHealth.engine and the
adapters-dict lookup key in get_adapter()/discover_engines() must match
EXACTLY, because BrokerRouter.route() calls
`get_adapter(h.engine, h.endpoint)` using the health object's own engine
field (runtime/router.py) — a mismatch (e.g. "tensorrt-llm" vs
"tensorrt_llm") would silently return None and skip metrics scraping for
that engine on every route decision.

All three are opt-in only: EngineEndpoints.lmdeploy/tensorrt_llm/lm_studio
default to "" and are excluded from to_dict() unless configured, so
discover_engines()'s zero-arg default path (used by demo/gate/status) is
completely unaffected.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch


class TestLMDeployAdapter(unittest.TestCase):
    def test_health_ready(self):
        from aictl.runtime.adapters import LMDeployAdapter
        with patch("aictl.runtime.adapters._http_get") as mock_get:
            mock_get.side_effect = [
                (200, ""),  # /health
                (200, json.dumps({"data": [{"id": "internlm2"}]})),  # /v1/models
            ]
            h = LMDeployAdapter("http://localhost:23333").health()
        self.assertEqual(h.engine, "lmdeploy")
        self.assertTrue(h.reachable)
        self.assertEqual(h.status, "READY")
        self.assertEqual(h.models, ["internlm2"])

    def test_health_unreachable(self):
        from aictl.runtime.adapters import LMDeployAdapter
        with patch("aictl.runtime.adapters._http_get", return_value=(0, "Connection refused")):
            h = LMDeployAdapter().health()
        self.assertFalse(h.reachable)
        self.assertEqual(h.status, "OFFLINE")

    def test_scrape_metrics_returns_basic_status_without_crashing(self):
        from aictl.runtime.adapters import LMDeployAdapter
        m = LMDeployAdapter().scrape_metrics()
        self.assertEqual(m.engine, "lmdeploy")

    def test_default_endpoint_uses_constant(self):
        from aictl.runtime.adapters import LMDeployAdapter
        from aictl.core.constants import LMDEPLOY_DEFAULT_URL
        self.assertEqual(LMDeployAdapter().endpoint, LMDEPLOY_DEFAULT_URL)


class TestTensorRTLLMAdapter(unittest.TestCase):
    def test_health_ready(self):
        from aictl.runtime.adapters import TensorRTLLMAdapter
        with patch("aictl.runtime.adapters._http_get") as mock_get:
            mock_get.side_effect = [
                (200, ""),
                (200, json.dumps({"data": [{"id": "llama3-70b"}]})),
            ]
            h = TensorRTLLMAdapter("http://localhost:8000").health()
        self.assertEqual(h.engine, "tensorrt_llm")
        self.assertTrue(h.reachable)
        self.assertEqual(h.models, ["llama3-70b"])

    def test_health_degraded_on_non_200(self):
        from aictl.runtime.adapters import TensorRTLLMAdapter
        with patch("aictl.runtime.adapters._http_get", return_value=(503, "")):
            h = TensorRTLLMAdapter().health()
        self.assertTrue(h.reachable)
        self.assertEqual(h.status, "DEGRADED")

    def test_default_endpoint_uses_constant(self):
        from aictl.runtime.adapters import TensorRTLLMAdapter
        from aictl.core.constants import TRT_LLM_DEFAULT_URL
        self.assertEqual(TensorRTLLMAdapter().endpoint, TRT_LLM_DEFAULT_URL)


class TestLMStudioAdapter(unittest.TestCase):
    def test_health_ready_from_models_endpoint_only(self):
        # LM Studio has no dedicated /health route -- /v1/models responding
        # IS the readiness signal (only one _http_get call expected).
        from aictl.runtime.adapters import LMStudioAdapter
        with patch("aictl.runtime.adapters._http_get",
                  return_value=(200, json.dumps({"data": [{"id": "qwen2.5-7b"}]}))) as mock_get:
            h = LMStudioAdapter("http://localhost:1234").health()
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(h.engine, "lm_studio")
        self.assertTrue(h.reachable)
        self.assertEqual(h.status, "READY")
        self.assertEqual(h.models, ["qwen2.5-7b"])

    def test_health_unreachable(self):
        from aictl.runtime.adapters import LMStudioAdapter
        with patch("aictl.runtime.adapters._http_get", return_value=(0, "refused")):
            h = LMStudioAdapter().health()
        self.assertFalse(h.reachable)

    def test_default_endpoint_uses_constant(self):
        from aictl.runtime.adapters import LMStudioAdapter
        from aictl.core.constants import LM_STUDIO_DEFAULT_URL
        self.assertEqual(LMStudioAdapter().endpoint, LM_STUDIO_DEFAULT_URL)


class TestEngineNameConsistency(unittest.TestCase):
    """The critical correctness point: EngineHealth.engine must exactly
    match the get_adapter()/discover_engines() lookup key, or
    BrokerRouter.route() silently drops metrics scraping for that engine."""

    def test_lmdeploy_health_engine_resolves_via_get_adapter(self):
        from aictl.runtime.adapters import LMDeployAdapter, get_adapter
        with patch("aictl.runtime.adapters._http_get", return_value=(200, "{}")):
            h = LMDeployAdapter("http://x").health()
        adapter = get_adapter(h.engine, h.endpoint)
        self.assertIsNotNone(adapter)
        self.assertIsInstance(adapter, LMDeployAdapter)

    def test_tensorrt_llm_health_engine_resolves_via_get_adapter(self):
        from aictl.runtime.adapters import TensorRTLLMAdapter, get_adapter
        with patch("aictl.runtime.adapters._http_get", return_value=(200, "{}")):
            h = TensorRTLLMAdapter("http://x").health()
        adapter = get_adapter(h.engine, h.endpoint)
        self.assertIsNotNone(adapter)
        self.assertIsInstance(adapter, TensorRTLLMAdapter)

    def test_lm_studio_health_engine_resolves_via_get_adapter(self):
        from aictl.runtime.adapters import LMStudioAdapter, get_adapter
        with patch("aictl.runtime.adapters._http_get", return_value=(200, "{}")):
            h = LMStudioAdapter("http://x").health()
        adapter = get_adapter(h.engine, h.endpoint)
        self.assertIsNotNone(adapter)
        self.assertIsInstance(adapter, LMStudioAdapter)

    def test_get_adapter_unknown_engine_returns_none(self):
        from aictl.runtime.adapters import get_adapter
        self.assertIsNone(get_adapter("nonexistent-engine", "http://x"))


class TestDiscoverEnginesWiring(unittest.TestCase):
    def test_discover_engines_includes_opt_in_engine_when_passed(self):
        from aictl.runtime.adapters import discover_engines
        with patch("aictl.runtime.adapters._http_get", return_value=(200, "{}")):
            results = discover_engines({"lmdeploy": "http://localhost:23333"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].engine, "lmdeploy")

    def test_default_discovery_excludes_opt_in_engines(self):
        # The whole point: zero-config discover_engines() (demo/gate/status)
        # must be completely unaffected by these new adapters existing.
        from aictl.runtime.adapters import discover_engines
        with patch("aictl.runtime.adapters._http_get", return_value=(0, "refused")) as mock_get:
            discover_engines(None)
        probed_engines = {call.args[0] for call in mock_get.call_args_list}
        for url_fragment in ("23333", "8000", "1234"):
            # 8000 is ambiguous with vLLM's own default port, so only check
            # the unambiguous new ports (lmdeploy 23333, lm_studio 1234).
            if url_fragment == "23333" or url_fragment == "1234":
                self.assertFalse(any(url_fragment in u for u in probed_engines))


class TestEngineEndpointsOptIn(unittest.TestCase):
    def test_new_fields_default_empty(self):
        from aictl.core.config import EngineEndpoints
        e = EngineEndpoints()
        self.assertEqual(e.lmdeploy, "")
        self.assertEqual(e.tensorrt_llm, "")
        self.assertEqual(e.lm_studio, "")

    def test_to_dict_excludes_unset_opt_in_engines(self):
        from aictl.core.config import EngineEndpoints
        d = EngineEndpoints().to_dict()
        self.assertNotIn("lmdeploy", d)
        self.assertNotIn("tensorrt_llm", d)
        self.assertNotIn("lm_studio", d)
        self.assertIn("vllm", d)
        self.assertIn("ollama", d)
        self.assertIn("sglang", d)

    def test_to_dict_includes_configured_opt_in_engine(self):
        from aictl.core.config import EngineEndpoints
        d = EngineEndpoints(lmdeploy="http://localhost:23333").to_dict()
        self.assertEqual(d["lmdeploy"], "http://localhost:23333")

    def test_config_roundtrip_persists_opt_in_engines(self):
        import tempfile
        from pathlib import Path
        from aictl.core.config import Config, EngineEndpoints, load_config, save_config
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            cfg = Config(engines=EngineEndpoints(tensorrt_llm="http://localhost:8001"))
            save_config(cfg, d)
            loaded = load_config(d)
            self.assertEqual(loaded.engines.tensorrt_llm, "http://localhost:8001")

    def test_default_config_load_unaffected_by_new_fields(self):
        # A config.json written before this pass (no lmdeploy/tensorrt_llm/
        # lm_studio keys at all) must still load cleanly with defaults.
        import tempfile, json as _json
        from pathlib import Path
        from aictl.core.config import load_config
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "config.json").write_text(_json.dumps({
                "engines": {"vllm": "http://custom:9000", "ollama": "http://localhost:11434",
                           "sglang": "http://localhost:30000"},
            }))
            cfg = load_config(d)
            self.assertEqual(cfg.engines.vllm, "http://custom:9000")
            self.assertEqual(cfg.engines.lmdeploy, "")


if __name__ == "__main__":
    unittest.main()
