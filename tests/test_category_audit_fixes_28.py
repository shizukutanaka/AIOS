"""Pass 28 regression tests: mock_engine IndexError on whitespace-only prompts."""

import unittest


class TestMockEngineWhitespacePrompt(unittest.TestCase):
    """mock_engine._generate_response must not raise on whitespace-only prompts."""

    def _generate(self, prompt: str) -> str:
        from aictl.daemon.mock_engine import _generate_response
        return _generate_response(prompt, max_tokens=50)

    def test_empty_string_does_not_raise(self):
        result = self._generate("")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_whitespace_only_does_not_raise(self):
        """Whitespace-only prompt caused IndexError before the fix."""
        result = self._generate("   ")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_newline_only_does_not_raise(self):
        result = self._generate("\n\t\r")
        self.assertIsInstance(result, str)

    def test_normal_prompt_still_works(self):
        result = self._generate("What is AI?")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_whitespace_and_normal_prompt(self):
        """Leading/trailing whitespace should not affect keyword matching."""
        result_trimmed = self._generate("hello")
        result_padded = self._generate("  hello  ")
        self.assertEqual(result_trimmed, result_padded)


if __name__ == "__main__":
    unittest.main()
