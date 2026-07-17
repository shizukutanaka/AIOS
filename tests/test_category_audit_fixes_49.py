"""Pass 49 regression tests: eval.py route_tier assertion type."""

import unittest


class TestEvalRouteTierAssertion(unittest.TestCase):
    """_check_assertion must handle route_tier type correctly."""

    def _check(self, assertion: dict, prompt: str) -> tuple:
        from aictl.cmd.eval import _check_assertion
        return _check_assertion(assertion, output="", latency_ms=0, cost_usd=0.0, prompt=prompt)

    def test_simple_prompt_classified_simple(self):
        """A trivial prompt should classify as SIMPLE."""
        passed, reason = self._check(
            {"type": "route_tier", "value": "SIMPLE"},
            "What is 2+2?",
        )
        self.assertTrue(passed, f"Expected SIMPLE classification, got: {reason}")

    def test_complex_prompt_classified_complex(self):
        """A detailed technical prompt should classify as COMPLEX."""
        passed, reason = self._check(
            {"type": "route_tier", "value": "COMPLEX"},
            "Explain the philosophical implications of quantum entanglement "
            "and compare to classical determinism across multiple academic schools.",
        )
        self.assertTrue(passed, f"Expected COMPLEX classification, got: {reason}")

    def test_wrong_tier_fails(self):
        """Asserting the wrong tier returns passed=False."""
        passed, reason = self._check(
            {"type": "route_tier", "value": "COMPLEX"},
            "What is 2+2?",
        )
        self.assertFalse(passed)

    def test_tier_case_insensitive(self):
        """route_tier value should be case-insensitive (simple → SIMPLE)."""
        passed, reason = self._check(
            {"type": "route_tier", "value": "simple"},
            "What is 2+2?",
        )
        self.assertTrue(passed, f"Case-insensitive check failed: {reason}")

    def test_reason_includes_score(self):
        """Reason string must include the score for debugging."""
        _, reason = self._check(
            {"type": "route_tier", "value": "SIMPLE"},
            "What is 2+2?",
        )
        self.assertIn("score=", reason)

    def test_unknown_type_still_fails_gracefully(self):
        """An unknown assertion type should return passed=False without crashing."""
        from aictl.cmd.eval import _check_assertion
        passed, reason = _check_assertion(
            {"type": "nonexistent_type", "value": "x"},
            output="some output",
            latency_ms=10,
            cost_usd=0.0,
            prompt="hello",
        )
        self.assertFalse(passed)
        self.assertIn("unknown", reason)

    def test_route_tier_does_not_use_llm_output(self):
        """route_tier checks the prompt, not the LLM output — output is ignored."""
        from aictl.cmd.eval import _check_assertion
        # Contradictory output should not affect route_tier result
        passed, _ = _check_assertion(
            {"type": "route_tier", "value": "SIMPLE"},
            output="COMPLEX COMPLEX COMPLEX",
            latency_ms=0,
            cost_usd=0.0,
            prompt="What is 2+2?",
        )
        self.assertTrue(passed)


if __name__ == "__main__":
    unittest.main()
