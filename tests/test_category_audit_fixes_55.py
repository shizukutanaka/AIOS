"""Pass 55 regression tests: kserve disagg guard, orchestrator/gateway port constants."""

import pathlib
import re
import unittest


class TestKServeDisaggReplicas1(unittest.TestCase):
    """kserve.py: replicas=1 must skip P/D disaggregation entirely."""

    def _build(self, replicas: int, enable_pd: bool = True):
        from aictl.stack.kserve import LLMISvcConfig, stack_to_llmisvc
        from aictl.stack.manifest import StackManifest, ServiceDef

        svc = ServiceDef(name="llm", runtime="vllm", model="meta-llama/Meta-Llama-3-8B")
        manifest = StackManifest(name="test", services=[svc])
        config = LLMISvcConfig(replicas=replicas, enable_pd_disagg=enable_pd)
        return stack_to_llmisvc(manifest, config, namespace="test")

    def test_replicas_1_no_disagg_key(self):
        resources = self._build(replicas=1)
        llmisvc = resources[0]
        self.assertNotIn("disaggregation", llmisvc["spec"])

    def test_replicas_2_has_disagg(self):
        resources = self._build(replicas=2)
        llmisvc = resources[0]
        self.assertIn("disaggregation", llmisvc["spec"])

    def test_replicas_2_prefill_plus_decode_le_replicas(self):
        resources = self._build(replicas=2)
        disagg = resources[0]["spec"]["disaggregation"]
        total = disagg["prefillReplicas"] + disagg["decodeReplicas"]
        self.assertLessEqual(total, 2)

    def test_replicas_3_prefill_plus_decode_le_replicas(self):
        resources = self._build(replicas=3)
        disagg = resources[0]["spec"]["disaggregation"]
        total = disagg["prefillReplicas"] + disagg["decodeReplicas"]
        self.assertLessEqual(total, 3)

    def test_disagg_disabled_no_key(self):
        resources = self._build(replicas=4, enable_pd=False)
        llmisvc = resources[0]
        self.assertNotIn("disaggregation", llmisvc["spec"])

    def test_decode_replicas_never_zero_for_any_valid_count(self):
        from aictl.stack.kserve import LLMISvcConfig, stack_to_llmisvc
        from aictl.stack.manifest import StackManifest, ServiceDef

        svc = ServiceDef(name="llm", runtime="vllm", model="meta-llama/Meta-Llama-3-8B")
        manifest = StackManifest(name="test", services=[svc])
        for r in range(2, 9):
            config = LLMISvcConfig(replicas=r, enable_pd_disagg=True)
            resources = stack_to_llmisvc(manifest, config)
            disagg = resources[0]["spec"]["disaggregation"]
            self.assertGreater(disagg["decodeReplicas"], 0,
                               f"decodeReplicas must be > 0 for replicas={r}")
            self.assertGreater(disagg["prefillReplicas"], 0,
                               f"prefillReplicas must be > 0 for replicas={r}")


class TestOrchestratorPortConstants(unittest.TestCase):
    """orchestrator.py must use constants, not hardcoded port numbers."""

    def _src(self):
        return (pathlib.Path(__file__).parent.parent / "aictl" / "stack" / "orchestrator.py").read_text()

    def test_no_hardcoded_ollama_port(self):
        src = self._src()
        # 11434 must not appear as a raw literal
        self.assertNotIn("11434", src, "orchestrator.py must not hardcode Ollama port 11434")

    def test_no_hardcoded_proxy_port(self):
        src = self._src()
        self.assertNotIn("or 8080", src, "orchestrator.py must not hardcode proxy port 8080")

    def test_no_hardcoded_vllm_port_literal(self):
        src = self._src()
        # 8000 used as a default literal "or 8000" must not exist
        self.assertNotIn("or 8000", src, "orchestrator.py must not hardcode vLLM port 8000 as 'or 8000'")

    def test_imports_constants(self):
        src = self._src()
        self.assertIn("from aictl.core.constants import", src)
        self.assertIn("VLLM_DEFAULT_PORT", src)

    def test_urlopen_used_as_context_manager(self):
        src = self._src()
        self.assertIn("with urllib.request.urlopen(", src,
                      "urlopen must be used as a context manager to prevent resource leak")


class TestGatewayPortConstants(unittest.TestCase):
    """gateway.py must use VLLM_DEFAULT_PORT constant, not a hardcoded literal."""

    def _src(self):
        return (pathlib.Path(__file__).parent.parent / "aictl" / "stack" / "gateway.py").read_text()

    def test_no_hardcoded_8000(self):
        src = self._src()
        self.assertNotIn("or 8000", src, "gateway.py must not hardcode port 8000 as 'or 8000'")

    def test_imports_vllm_default_port(self):
        src = self._src()
        self.assertIn("VLLM_DEFAULT_PORT", src)


if __name__ == "__main__":
    unittest.main()
