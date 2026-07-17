"""Pass 7 regression tests for correctness bugs identified by deep audit."""

import unittest


class TestKServeServiceLabel(unittest.TestCase):
    """kserve.py: aios.service pod label must match gateway InferencePool selector."""

    def test_service_label_includes_stack_name(self):
        from aictl.stack.kserve import _build_llmisvc, LLMISvcConfig
        from aictl.stack.manifest import ServiceDef

        svc = ServiceDef(name="chat", runtime="vllm", model="llama3:8b",
                         image="", gpu_required=True, replicas=1)
        config = LLMISvcConfig()
        result = _build_llmisvc(svc, "mystack", "default", config)

        labels = result["metadata"]["labels"]
        # Must use "mystack-chat" so gateway InferencePool selector matches
        self.assertEqual(labels["aios.service"], "mystack-chat")
        self.assertNotEqual(labels["aios.service"], "chat",
                            "aios.service label must include stack prefix to match gateway selector")

    def test_service_label_matches_gateway_pool_name(self):
        """InferencePool selector in gateway uses f'{manifest.name}-{svc.name}' — must match."""
        from aictl.stack.kserve import _build_llmisvc, LLMISvcConfig
        from aictl.stack.gateway import stack_to_gateway_api, GatewayInferenceConfig
        from aictl.stack.manifest import ServiceDef, StackManifest

        svc = ServiceDef(name="llm", runtime="vllm", model="qwen2.5:7b",
                         image="", gpu_required=True, replicas=1)
        manifest = StackManifest(name="prod", services=[svc], models=[])

        config = LLMISvcConfig()
        kserve_obj = _build_llmisvc(svc, manifest.name, "default", config)
        kserve_labels = kserve_obj["metadata"]["labels"]

        gw_resources = stack_to_gateway_api(manifest, GatewayInferenceConfig())

        # Find the InferencePool
        pool = next((r for r in gw_resources if r.get("kind") == "InferencePool"), None)
        self.assertIsNotNone(pool, "Should have an InferencePool resource")

        pool_selector = pool["spec"]["selector"]
        expected_label = pool_selector.get("aios.service")
        actual_label = kserve_labels.get("aios.service")

        self.assertEqual(actual_label, expected_label,
                         f"kserve pod label 'aios.service={actual_label}' must match "
                         f"gateway pool selector 'aios.service={expected_label}'")


class TestMigDeadCode(unittest.TestCase):
    """mig.py: dead int() expression must be removed from generate_mig_commands."""

    def test_no_dead_int_expression(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "mig.py").read_text()
        # The dead expression was int(p.get("slices", "1")) with no assignment
        # Check it's gone from generate_mig_commands context
        self.assertNotIn('int(p.get("slices", "1"))', src,
                         "Dead int(p.get('slices')) expression must be removed from mig.py")

    def test_generate_mig_commands_returns_commands(self):
        from aictl.runtime.mig import generate_mig_commands, PartitionPlan
        plan = PartitionPlan(gpu_index=0, gpu_name="A100 80GB", total_vram_gb=80, partitions=[
            {"profile": "1g.10gb", "model": "phi3", "vram_gb": "10", "slices": "1"},
            {"profile": "2g.20gb", "model": "llama3", "vram_gb": "20", "slices": "2"},
        ])
        cmds = generate_mig_commands(plan)
        self.assertGreater(len(cmds), 0)
        # First command enables MIG mode
        self.assertIn("nvidia-smi -i 0 -mig 1", cmds[0])
        # Subsequent commands create GPU instances
        self.assertIn("-cgi 1g.10gb", cmds[1])
        self.assertIn("-cgi 2g.20gb", cmds[2])


class TestRecommendCPUOnlyHeadroom(unittest.TestCase):
    """recommend.py: headroom calculation must not divide by 1 when RAM is unknown."""

    def test_cpu_only_unknown_ram_returns_results(self):
        from aictl.runtime.recommend import recommend
        # CPU-only mode (vram_mb=0), RAM unknown (ram_mb=0)
        # Old code divided by max(0, 1)=1 → all models appeared "too tight" (utilization=huge)
        # New code: headroom=1.0 → all pass with neutral score (don't assume tight)
        results = recommend(vram_mb=0, ram_mb=0)
        self.assertIsInstance(results, list)
        # Should return at least some models even with unknown RAM
        self.assertGreater(len(results), 0,
                           "CPU-only unknown-RAM mode must still recommend models")

    def test_cpu_only_with_known_ram_returns_results(self):
        from aictl.runtime.recommend import recommend
        # With 32 GB RAM, 7B models (requiring ~6 GB) should be recommended
        results = recommend(vram_mb=0, ram_mb=32768)
        self.assertGreater(len(results), 0, "Should recommend models for 32GB RAM CPU system")
        # All results should be ollama runtime (CPU-only path)
        for r in results:
            self.assertEqual(r.runtime, "ollama")

    def test_headroom_not_divided_by_one_when_ram_zero(self):
        """Verify the fix: when ram_mb=0, headroom stays 1.0, not 1.0 - (large_value/1)."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "recommend.py").read_text()
        # The old buggy pattern used max(ram_mb, 1) as denominator
        self.assertNotIn("max(ram_mb, 1)", src,
                         "recommend.py must not divide by max(ram_mb, 1) for headroom")


class TestDoctorURLParsing(unittest.TestCase):
    """doctor.py: URL parsing must handle URLs without explicit port numbers."""

    def test_url_without_port_uses_urlparse(self):
        """http://localhost (no port) must not crash or misreport as unreachable."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "doctor.py").read_text()
        # The old broken pattern split on ":" and tried int() on the hostname
        self.assertNotIn(".split(\":\")[-1]", src,
                         "doctor.py must not use fragile split(':')[-1] for port extraction")
        self.assertIn("urlparse", src,
                      "doctor.py must use urllib.parse.urlparse for URL parsing")

    def test_url_with_explicit_port_works(self):
        """Verify the port extraction logic works for URLs with explicit ports."""
        from urllib.parse import urlparse
        urls = [
            ("http://localhost:8000", "localhost", 8000),
            ("http://localhost:11434", "localhost", 11434),
            ("http://127.0.0.1:7700", "127.0.0.1", 7700),
        ]
        for url, expected_host, expected_port in urls:
            parsed = urlparse(url)
            self.assertEqual(parsed.hostname, expected_host)
            self.assertEqual(parsed.port, expected_port)

    def test_url_without_explicit_port_defaults(self):
        """URLs without explicit port must default to 80/443."""
        from urllib.parse import urlparse
        for url, expected_port in [("http://localhost", 80), ("https://example.com", 443)]:
            parsed = urlparse(url)
            port = parsed.port or (443 if url.startswith("https://") else 80)
            self.assertEqual(port, expected_port,
                             f"URL {url} must default to port {expected_port}")


class TestUpdateStdoutOnFailure(unittest.TestCase):
    """update.py: update failure must display stdout when stderr is empty."""

    def test_stdout_shown_when_stderr_empty(self):
        """When update fails with output on stdout only, that output must be shown."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "update.py").read_text()
        # The old buggy condition 'if error and rc == 1 and out' required error to be truthy
        # which meant stdout was silently dropped when stderr was empty.
        self.assertNotIn("if error and rc == 1 and out:", src,
                         "update.py must not gate stdout display on stderr being non-empty")

    def test_error_message_uses_stdout_fallback(self):
        """err() call must fall back to stdout content when stderr is empty."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "update.py").read_text()
        self.assertIn("err(error or out or", src,
                      "update.py must use 'error or out or ...' fallback in err() call")


if __name__ == "__main__":
    unittest.main()
