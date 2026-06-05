"""Tests for SDK validation improvements and project completeness."""

import io
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestSDKValidationComplete(unittest.TestCase):
    """All SDK public methods must validate inputs and give actionable errors."""

    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def _import(self):
        import aictl
        return aictl

    def test_ask_empty_string_raises(self):
        a = self._import()
        with self.assertRaises(ValueError) as cm:
            a.ai.ask('')
        self.assertIn("empty", str(cm.exception).lower())

    def test_ask_whitespace_only_raises(self):
        a = self._import()
        with self.assertRaises(ValueError):
            a.ai.ask('   \n\t  ')

    def test_ask_none_raises_with_hint(self):
        a = self._import()
        with self.assertRaises(TypeError) as cm:
            a.ai.ask(None)
        msg = str(cm.exception)
        self.assertIn("str", msg)
        self.assertIn("Try", msg)

    def test_ask_int_raises_with_hint(self):
        a = self._import()
        with self.assertRaises(TypeError) as cm:
            a.ai.ask(42)
        msg = str(cm.exception)
        self.assertIn("int", msg)
        self.assertIn("Try", msg)

    def test_classify_none_raises_with_hint(self):
        a = self._import()
        with self.assertRaises(TypeError) as cm:
            a.ai.classify(None, categories=['a'])
        self.assertIn("Try", str(cm.exception))

    def test_classify_empty_categories_raises_with_hint(self):
        a = self._import()
        with self.assertRaises(ValueError) as cm:
            a.ai.classify("text", categories=[])
        self.assertIn("Try", str(cm.exception))

    def test_embed_none_raises_with_hint(self):
        a = self._import()
        with self.assertRaises(TypeError) as cm:
            a.ai.embed(None)
        self.assertIn("Try", str(cm.exception))

    def test_configure_negative_budget_raises(self):
        a = self._import()
        with self.assertRaises(ValueError) as cm:
            a.ai.configure(cost_budget_usd=-10.0)
        msg = str(cm.exception)
        self.assertIn("0", msg)

    def test_configure_zero_budget_allowed(self):
        """Zero budget is valid (disables spending)."""
        a = self._import()
        a.ai.configure(cost_budget_usd=0)  # should not raise
        self.assertIsNone(None)  # reaches here without error

    def test_configure_positive_budget_allowed(self):
        a = self._import()
        a.ai.configure(cost_budget_usd=100.0)  # should not raise
        self.assertIsNone(None)  # reaches here without error

    def test_configure_wrong_type_raises(self):
        a = self._import()
        with self.assertRaises(TypeError):
            a.ai.configure(cost_budget_usd="100")

    def test_ask_valid_string_works(self):
        a = self._import()
        r = a.ai.ask("What is AI?")
        self.assertIsNotNone(r)
        self.assertGreater(len(str(r)), 0)

    def test_ask_with_context_works(self):
        a = self._import()
        r = a.ai.ask("Summarize", context="AI is a field of computer science.")
        self.assertGreater(len(str(r)), 0)

    def test_classify_valid_works(self):
        a = self._import()
        result = a.ai.classify("I love this!", categories=["positive", "negative"])
        self.assertIn(result, ["positive", "negative"])


class TestProjectCompleteness(unittest.TestCase):
    """Verify all required project files exist and have content."""

    PROJECT_ROOT = Path(__file__).parent.parent

    def test_security_md_exists(self):
        path = self.PROJECT_ROOT / "SECURITY.md"
        self.assertTrue(path.exists(), "SECURITY.md is required for GitHub public repos")
        text = path.read_text()
        self.assertGreater(len(text), 100)

    def test_security_md_has_contact(self):
        text = (self.PROJECT_ROOT / "SECURITY.md").read_text()
        self.assertTrue(
            "security" in text.lower() or "email" in text.lower() or "advisory" in text.lower(),
            "SECURITY.md must explain how to report vulnerabilities"
        )

    def test_contributing_md_exists(self):
        path = self.PROJECT_ROOT / "CONTRIBUTING.md"
        self.assertTrue(path.exists(), "CONTRIBUTING.md should exist")

    def test_license_exists(self):
        path = self.PROJECT_ROOT / "LICENSE"
        self.assertTrue(path.exists(), "LICENSE file is required")
        text = path.read_text()
        self.assertIn("MIT", text)

    def test_pyproject_has_keywords(self):
        import tomllib
        with open(self.PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        keywords = cfg.get("project", {}).get("keywords", [])
        self.assertGreater(len(keywords), 3, "pyproject.toml needs keywords for PyPI SEO")
        self.assertIn("ai", keywords)
        self.assertIn("cli", keywords)

    def test_pyproject_has_homepage_url(self):
        import tomllib
        with open(self.PROJECT_ROOT / "pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        urls = cfg.get("project", {}).get("urls", {})
        self.assertIn("Homepage", urls, "pyproject.toml should have Homepage URL")

    def test_readme_has_security_mention(self):
        readme = (self.PROJECT_ROOT / "README.md").read_text()
        # Should mention security or SECURITY.md
        self.assertTrue(
            "SECURITY.md" in readme or "security" in readme.lower(),
            "README should mention security practices"
        )

    def test_github_issue_template_exists(self):
        templates = self.PROJECT_ROOT / ".github" / "ISSUE_TEMPLATE"
        self.assertTrue(templates.exists(), ".github/ISSUE_TEMPLATE should exist")


class TestSDKResponseBehavior(unittest.TestCase):
    """_Response must behave exactly like str in all common operations."""

    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_response_add_str(self):
        import aictl
        r = aictl.ai.ask("hello")
        result = r + "!"
        self.assertIsInstance(result, str)
        self.assertTrue(result.endswith("!"))

    def test_str_add_response(self):
        import aictl
        r = aictl.ai.ask("hello")
        result = "Hi: " + r
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("Hi: "))

    def test_response_eq_str(self):
        import aictl
        r = aictl.ai.ask("hello")
        self.assertEqual(r, str(r))

    def test_response_in_fstring(self):
        import aictl
        r = aictl.ai.ask("hello")
        formatted = f"Answer: {r}"
        self.assertTrue(formatted.startswith("Answer: "))

    def test_response_strip(self):
        import aictl
        r = aictl.ai.ask("hello")
        stripped = r.strip()
        self.assertIsInstance(stripped, str)

    def test_response_split(self):
        import aictl
        r = aictl.ai.ask("hello")
        parts = r.split()
        self.assertIsInstance(parts, list)

    def test_response_lower(self):
        import aictl
        r = aictl.ai.ask("hello")
        lowered = r.lower()
        self.assertIsInstance(lowered, str)
        self.assertEqual(lowered, str(r).lower())

    def test_response_len(self):
        import aictl
        r = aictl.ai.ask("hello")
        self.assertEqual(len(r), len(str(r)))

    def test_response_bool_nonempty(self):
        import aictl
        r = aictl.ai.ask("hello")
        # Non-empty response should be truthy when converted to str
        self.assertTrue(bool(str(r)))


if __name__ == "__main__":
    unittest.main()
