"""Tests for the extended SDK surface: classify, structured, configure, context."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestSdkBasicSurface(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_imports_clean(self):
        import aictl
        self.assertTrue(hasattr(aictl, "ai"))
        self.assertTrue(hasattr(aictl, "__version__"))

    def test_ai_has_expected_methods(self):
        import aictl
        for name in ["ask", "chat", "embed", "classify", "structured", "configure"]:
            self.assertTrue(
                hasattr(aictl.ai, name),
                f"aictl.ai is missing method: {name}",
            )

    def test_status_returns_dict(self):
        import aictl
        s = aictl.ai.status
        self.assertIsInstance(s, dict)


class TestAsk(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_ask_basic(self):
        import aictl
        r = aictl.ai.ask("hello")
        # Response acts like a string
        self.assertIsInstance(str(r), str)
        self.assertGreater(len(str(r)), 0)

    def test_ask_with_context(self):
        import aictl
        r = aictl.ai.ask(
            "What does this email mean?",
            context="The shipment was delayed due to weather.",
        )
        self.assertIsInstance(str(r), str)

    def test_ask_modes(self):
        """All four documented modes should run without error."""
        import aictl
        for mode in ["default", "reasoning", "creative", "factual", "concise"]:
            r = aictl.ai.ask("test", mode=mode)
            self.assertIsInstance(str(r), str)


class TestClassify(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_classify_returns_one_of_categories(self):
        import aictl
        cats = ["positive", "negative", "neutral"]
        result = aictl.ai.classify("I love this!", categories=cats)
        self.assertIn(result, cats)

    def test_classify_empty_categories_raises(self):
        import aictl
        with self.assertRaises(ValueError):
            aictl.ai.classify("test", categories=[])

    def test_classify_single_category(self):
        """Even a single category should return that category."""
        import aictl
        result = aictl.ai.classify("anything", categories=["only_choice"])
        self.assertEqual(result, "only_choice")

    def test_classify_handles_partial_match(self):
        """Model might return 'positive feedback' for 'positive' category."""
        import aictl
        # This relies on the substring matching in classify()
        result = aictl.ai.classify(
            "This is wonderful!",
            categories=["positive", "negative"],
        )
        self.assertIn(result, ["positive", "negative"])


class TestStructured(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_structured_method_exists(self):
        import aictl
        self.assertTrue(callable(aictl.ai.structured))

    def test_structured_invalid_json_raises_value_error(self):
        """The mock engine returns fixed text. structured() must raise
        ValueError when JSON parsing fails (rather than crashing)."""
        import aictl
        with self.assertRaises(ValueError):
            aictl.ai.structured(
                "Extract name and age",
                schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
                context="Alice is 30.",
            )


class TestConfigure(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_configure_budget(self):
        import aictl
        # Should not raise
        aictl.ai.configure(cost_budget_usd=10.50)
        self.assertIsNone(aictl.ai.configure(cost_budget_usd=10.50))

    def test_configure_preference(self):
        import aictl
        for prefer in ["speed", "quality", "cost"]:
            aictl.ai.configure(prefer=prefer)
        self.assertIsNone(aictl.ai.configure(prefer=prefer))

    def test_configure_no_args_is_noop(self):
        import aictl
        # Calling configure with nothing should not raise
        aictl.ai.configure()
        self.assertIsNone(aictl.ai.configure())


class TestEmbed(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_embed_single_string(self):
        import aictl
        result = aictl.ai.embed("hello world")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        # Each embedding is a list of floats
        self.assertTrue(all(isinstance(x, (int, float)) for x in result[0]))

    def test_embed_multiple_strings(self):
        import aictl
        result = aictl.ai.embed(["one", "two", "three"])
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 3)


if __name__ == "__main__":
    unittest.main()
