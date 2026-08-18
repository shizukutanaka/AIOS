"""Pass 208: the SDK must not silently fabricate answers.

Found by following Musk step 1 — question the requirement — into the error
taxonomy. Eight of nine exception classes are never raised, and the obvious
reading was "delete them". But the requirement they serve ("an error says what
happened and what to do") is a good one with a name attached, so the right
question became: where *should* they fire and don't?

The answer was worse than an unused class. With no engine running,
`aictl.ai.ask()` did not fail at all — it started an in-process mock, whose
docstring said the substitution was "invisible to the developer", and returned:

  * plausible generated text,
  * attributed to a real hardware-derived model name (`llama3.2:1b`),
  * with a non-zero cost for inference that never happened.

A user could `import aictl`, get confident answers, ship it, and never learn
their output was fabricated. `tco`/`cost` would report spend that did not
occur. This is the same defect class the rest of this session kept fixing —
undisclosed degradation — sitting at the primary library entry point.

The fix keeps the convenience and removes the deception. Zero-config
`import aictl; aictl.ai.ask(...)` still works with nothing installed, which is
a genuine feature; the response now carries `mock=True`, names the model
"mock", and reports zero cost.

Notably, an existing test asserted `cost_usd > 0` while running against the
mock — it *encoded* the fabrication rather than catching it. Corrected here.
"""

from __future__ import annotations

import unittest

from tests.support import IsolatedStateTestCase


class TestMockIsDisclosed(IsolatedStateTestCase):
    """No engine is configured in tests, so the mock serves these."""

    def _ask(self, prompt="What is 2+2?"):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()
        import aictl
        return aictl.ai.ask(prompt)

    def test_mock_served_responses_disclose_it(self):
        # One-directional on purpose. "Mock-served implies disclosed" is the
        # requirement. The converse is not: `AICTL_MODEL` lets a user point a
        # real engine at a model they have named "mock", and calling that a
        # violation would be asserting a naming coincidence. An earlier draft
        # of this test asserted the biconditional and failed for exactly that
        # reason — the test was wrong, not the code.
        response = self._ask()
        if response.mock:
            self.assertEqual(response.model, "mock")
            self.assertEqual(response.cost_usd, 0.0)
            self.assertIn("MOCK", repr(response))

    def test_model_is_not_misattributed(self):
        # Naming a real model for text a mock produced is the specific
        # misattribution this exists to prevent.
        response = self._ask()
        if response.mock:
            self.assertEqual(response.model, "mock")

    def test_no_cost_is_billed_for_inference_that_did_not_happen(self):
        # A non-zero figure here surfaces in tco/cost reporting as spend that
        # never occurred.
        response = self._ask()
        if response.mock:
            self.assertEqual(response.cost_usd, 0.0)
            self.assertEqual(response.cost_jpy, 0.0)

    def test_repr_announces_the_mock(self):
        # The repr is what a developer sees at a REPL or in a log.
        response = self._ask()
        self.assertEqual("MOCK" in repr(response), response.mock)

    def test_text_is_still_returned(self):
        # Disclosure, not removal: zero-config still has to work.
        response = self._ask()
        self.assertTrue(str(response).strip())

    def test_status_reports_the_mock(self):
        # "What am I actually running against?" is the question status()
        # exists to answer, and the mock is the surprising answer.
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()
        import aictl
        response = aictl.ai.ask("hello")
        self.assertEqual(aictl.ai.status["mock"], response.mock)

    def test_status_mock_key_always_present(self):
        # Stable shape: False means "a real engine", never "not checked".
        import aictl
        self.assertIn("mock", aictl.ai.status)

    def test_context_property_agrees_with_the_response(self):
        from aictl.sdk import _AmbientContext
        response = self._ask()
        self.assertEqual(_AmbientContext().model, response.model)


class TestFlagDefaults(unittest.TestCase):
    def test_response_defaults_to_not_mock(self):
        # A directly-constructed response must not claim to be a mock; only
        # the path that actually used one sets it.
        from aictl.sdk import _Response

        self.assertFalse(_Response(text="hi").mock)

    def test_real_engine_response_would_not_be_flagged(self):
        from aictl.sdk import _Response

        response = _Response(text="hi", model="llama3.1:8b", cost_usd=0.01)
        self.assertFalse(response.mock)
        self.assertNotIn("MOCK", repr(response))

    def test_mock_flag_survives_dataclass_round_trip(self):
        from dataclasses import asdict

        from aictl.sdk import _Response

        self.assertTrue(asdict(_Response(text="x", mock=True))["mock"])


class TestTestSuiteNoLongerEncodesTheBug(unittest.TestCase):
    def test_cost_story_does_not_demand_cost_from_a_mock(self):
        from pathlib import Path

        source = Path("tests/test_e2e_stories.py").read_text()
        start = source.index("def test_cost_tracking_flow")
        body = source[start:start + 1800]
        self.assertIn("if r1.mock:", body,
                      "the cost story must branch on whether a real engine "
                      "served it, rather than requiring the mock to bill")


if __name__ == "__main__":
    unittest.main()
