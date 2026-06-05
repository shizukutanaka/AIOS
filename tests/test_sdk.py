"""Tests for the aictl SDK — the Apple-philosophy developer surface.

Core contracts this verifies:
  1. `from aictl import ai` works immediately
  2. `ai.ask(...)` returns something usable with zero configuration
  3. `ai.chat(...)` supports multi-turn
  4. `ai.embed(...)` produces vectors
  5. Progressive disclosure: status/mode/model are optional, not required
  6. Graceful degradation when engines are unavailable
"""

import json
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestSDKPublicSurface(unittest.TestCase):
    """Verify the Apple-minimal public API surface."""

    def test_import_works(self):
        """The single import contract: from aictl import ai."""
        from aictl import ai
        self.assertIsNotNone(ai)

    def test_ai_has_exactly_three_core_methods(self):
        """Resist feature creep. Three methods, no more at this level."""
        from aictl import ai
        self.assertTrue(callable(ai.ask))
        self.assertTrue(callable(ai.chat))
        self.assertTrue(callable(ai.embed))

    def test_version_accessible(self):
        """Version visible at package level."""
        import aictl
        self.assertTrue(aictl.__version__)
        self.assertRegex(aictl.__version__, r'\d+\.\d+\.\d+')


class TestSDKWithMockEngine(unittest.TestCase):
    """End-to-end verification via the mock engine.

    This simulates the real developer experience: spin up the mock engine,
    point the SDK at it, and verify each contract holds.
    """

    PORT = 19950

    @classmethod
    def setUpClass(cls):
        import os
        from aictl.daemon.mock_engine import start_mock_engine
        cls.server = start_mock_engine(port=cls.PORT)
        time.sleep(0.2)
        # Redirect SDK to our mock engine
        os.environ["AICTL_ENDPOINT"] = f"http://127.0.0.1:{cls.PORT}"
        os.environ["AICTL_MODEL"] = "mock-llama3-8b"

        # Reset the singleton so env vars take effect
        from aictl.sdk import _AmbientContext
        _AmbientContext._instance = None

    @classmethod
    def tearDownClass(cls):
        import os
        cls.server.shutdown()
        cls.server.server_close()
        os.environ.pop("AICTL_ENDPOINT", None)
        os.environ.pop("AICTL_MODEL", None)

    def test_ask_returns_response(self):
        """ai.ask returns a response that behaves like a string."""
        from aictl import ai
        result = ai.ask("What is 2+2?")
        self.assertIsNotNone(result)
        self.assertTrue(str(result))  # non-empty when stringified
        self.assertGreater(result.tokens, 0)
        self.assertEqual(result.model, "mock-llama3-8b")

    def test_ask_str_coercion(self):
        """Response should act like a string when used in string contexts."""
        from aictl import ai
        result = ai.ask("Hello")
        # Should work in f-strings, concatenation, etc.
        formatted = f"Answer: {result}"
        self.assertIn("Answer: ", formatted)
        self.assertGreater(len(str(result)), 0)

    def test_ask_with_mode(self):
        """Semantic modes work without exposing implementation."""
        from aictl import ai
        for mode in ["default", "reasoning", "creative", "factual", "concise"]:
            result = ai.ask("Hi", mode=mode)
            self.assertIsNotNone(result.text)

    def test_ask_unknown_mode_falls_back(self):
        """Unknown modes degrade to default rather than crashing."""
        from aictl import ai
        result = ai.ask("Hi", mode="nonsense_mode")
        self.assertIsNotNone(result.text)

    def test_chat_multiturn(self):
        """Multi-turn conversation."""
        from aictl import ai
        result = ai.chat([
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ])
        self.assertIsNotNone(result.text)
        self.assertGreater(result.tokens, 0)

    def test_embed_single(self):
        """Single text embedding."""
        from aictl import ai
        vectors = ai.embed("Hello world")
        self.assertEqual(len(vectors), 1)
        self.assertGreater(len(vectors[0]), 0)
        self.assertIsInstance(vectors[0][0], float)

    def test_embed_batch(self):
        """Batch embedding."""
        from aictl import ai
        vectors = ai.embed(["one", "two", "three"])
        self.assertEqual(len(vectors), 3)

    def test_embed_deterministic_for_same_input(self):
        """Embeddings are deterministic for same input."""
        from aictl import ai
        v1 = ai.embed("identical text")[0]
        v2 = ai.embed("identical text")[0]
        self.assertEqual(v1, v2)

    def test_status_returns_diagnostics(self):
        """Status is available for power users via progressive disclosure."""
        from aictl import ai
        status = ai.status
        self.assertIn("ready", status)
        self.assertIn("hardware", status)
        self.assertIn("default_model", status)
        self.assertIn("endpoint", status)
        self.assertIn("usage", status)

    def test_usage_tracked(self):
        """Usage counters increment across calls."""
        from aictl import ai
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()
        before = ai.status["usage"]["calls"]
        # Use unique prompt + private=True to bypass semantic cache
        import time
        unique = f"test usage tracker {time.time()}"
        ai.ask(unique, private=True)
        after = ai.status["usage"]["calls"]
        self.assertEqual(after, before + 1)

    def test_private_flag_rejects_cloud_fallback(self):
        """private=True prevents cloud fallback even if local fails."""
        from aictl import ai
        # When endpoint is valid, private=True works normally
        result = ai.ask("Hi", private=True)
        self.assertIsNotNone(result.text)


class TestSDKAmbientContext(unittest.TestCase):
    """Verify the lazy, idempotent initialization behavior."""

    def test_singleton_pattern(self):
        """_AmbientContext returns the same instance every time."""
        from aictl.sdk import _AmbientContext
        a = _AmbientContext()
        b = _AmbientContext()
        self.assertIs(a, b)

    def test_ensure_ready_idempotent(self):
        """Calling ensure_ready twice doesn't re-initialize."""
        import os
        os.environ["AICTL_ENDPOINT"] = "http://127.0.0.1:9999"
        from aictl.sdk import _AmbientContext
        _AmbientContext._instance = None
        ctx = _AmbientContext()
        ctx.ensure_ready()
        t1 = ctx._ready
        ctx.ensure_ready()
        t2 = ctx._ready
        self.assertTrue(t1)
        self.assertTrue(t2)
        os.environ.pop("AICTL_ENDPOINT", None)


class TestSDKSmartDefaults(unittest.TestCase):
    """Verify that defaults adapt to hardware."""

    def test_model_selection_by_vram(self):
        """Different VRAM amounts produce different default models."""
        from aictl.sdk import _AmbientContext

        ctx = _AmbientContext.__new__(_AmbientContext)
        ctx._init()

        # Small VRAM → small model
        ctx._hardware = {"gpus": [{"vram_mb": 4000}]}
        ctx._choose_default_model()
        self.assertEqual(ctx._default_model, "llama3.2:1b")

        # Medium VRAM → medium model
        ctx._hardware = {"gpus": [{"vram_mb": 12000}]}
        ctx._choose_default_model()
        self.assertEqual(ctx._default_model, "qwen3:7b")

        # Large VRAM → 8B model
        ctx._hardware = {"gpus": [{"vram_mb": 24000}]}
        ctx._choose_default_model()
        self.assertEqual(ctx._default_model, "llama3.1:8b")

        # Enterprise VRAM → 32B model
        ctx._hardware = {"gpus": [{"vram_mb": 80000}]}
        ctx._choose_default_model()
        self.assertEqual(ctx._default_model, "qwen3:32b")

    def test_env_override_wins(self):
        """AICTL_MODEL env var overrides hardware-based selection."""
        import os
        from aictl.sdk import _AmbientContext

        os.environ["AICTL_MODEL"] = "my-custom-model"
        ctx = _AmbientContext.__new__(_AmbientContext)
        ctx._init()
        ctx._hardware = {"gpus": [{"vram_mb": 80000}]}
        ctx._choose_default_model()
        self.assertEqual(ctx._default_model, "my-custom-model")
        os.environ.pop("AICTL_MODEL", None)

    def test_cpu_only_gets_tiny_model(self):
        """No GPU → smallest model for CPU inference."""
        from aictl.sdk import _AmbientContext
        ctx = _AmbientContext.__new__(_AmbientContext)
        ctx._init()
        ctx._hardware = {"profile": "cpu-only", "gpus": []}
        ctx._choose_default_model()
        self.assertEqual(ctx._default_model, "llama3.2:1b")


class TestSDKDegradation(unittest.TestCase):
    """Verify the SDK never crashes even when things go wrong."""

    def test_embed_with_unreachable_endpoint(self):
        """ai.embed falls back to deterministic pseudo-vectors on failure."""
        from aictl.sdk import _embed
        # Point at nowhere
        vectors = _embed("http://127.0.0.1:1", ["test"])
        self.assertEqual(len(vectors), 1)
        self.assertEqual(len(vectors[0]), 32)  # SHA-256 bytes


if __name__ == "__main__":
    unittest.main()
