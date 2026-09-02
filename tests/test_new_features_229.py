"""Pass 229: classify() promised to flag an unknown answer and flagged nothing.

Running the shipped SDK examples — the last user-facing path nobody had
executed — produced this:

    [ positive]  I've been on hold for an hour. This is unacceptable.
    [ positive]  How do I reset my password?
    [ positive]  Why is my last payment showing as failed?

Every message, "positive". The cause was three lines in `classify()`:

    # Last resort: return the first category but flag unknown
    return categories[0]

**Nothing was flagged.** When the model's answer matched no category the caller
got `categories[0]`, indistinguishable from a confident correct classification.
The comment described behaviour the code did not implement — the same shape as
the gate's discarded command set and the installer's discarded interpreter,
except here it silently produced wrong answers rather than merely failing.

`classify()` returns a `str`, which is *why* the flag went missing: there was
nowhere to put it. `Classification` subclasses `str`, so every existing caller
is unaffected — comparisons, f-strings, JSON, dict keys — while `.matched` and
`.mock` are there for callers who care. That mirrors what `ask()` already does
with `_Response.mock`.

Three further defects in the examples themselves, all found by running them:

  * **None of the five could be run as documented.** `python3
    examples/sdk/01_classify.py` puts `examples/sdk/` on `sys.path`, not the
    repository root, so `import aictl` failed — and the README's install is a
    bare `git clone`, with no `pip install` step to make it importable.
  * **`05_cost.py` called `aictl.ai.status()`.** `status` is a `@property`;
    `docs/SDK.md` documents it correctly without parentheses. The example
    raised `TypeError: 'dict' object is not callable` and never worked.
  * **`02_extract.py` promised to run with no setup and could not.**
    `structured()` needs JSON and the in-process mock returns prose. The error
    said "Model did not return valid JSON", which sends a reader hunting for a
    schema bug rather than telling them no engine is running.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

EXAMPLES = sorted(Path("examples/sdk").glob("*.py"))
# 02_extract needs a real engine; its docstring now says so.
NEEDS_ENGINE = {"02_extract.py"}


class TestClassifyReportsWhetherItMatched(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext

        _AmbientContext.reset_for_testing()

    def test_an_unmatched_answer_is_flagged(self):
        # The bug: this returned a bare "positive" with no way to tell.
        import aictl

        result = aictl.ai.classify("I've been on hold for an hour.",
                                   categories=["positive", "complaint"])
        self.assertFalse(result.matched)

    def test_it_is_still_a_plain_string(self):
        # Every existing caller must be unaffected by the richer return.
        import aictl

        result = aictl.ai.classify("hello", categories=["a", "b"])
        self.assertIsInstance(result, str)
        self.assertEqual(result, str(result))
        self.assertEqual({result: 1}[str(result)], 1)

    def test_it_still_returns_one_of_the_categories(self):
        import aictl

        categories = ["positive", "complaint", "question"]
        self.assertIn(aictl.ai.classify("anything", categories=categories),
                      categories)

    def test_a_matched_answer_says_so(self):
        from aictl.sdk import Classification

        self.assertTrue(Classification("positive").matched)

    def test_classify_propagates_the_mock_flag_from_ask(self):
        # The invariant this pass added: classify() builds on ask(), so it
        # reports whatever ask() reported.
        #
        # Written hermetically, by stubbing ask(). Two earlier versions of this
        # test read live engine state — first `ai.status["mock"]`, then a real
        # `ask()` call — and both passed alone while failing inside the suite,
        # because the ambient context is a process-wide singleton that other
        # tests reset and re-detect against whatever ports happen to be open.
        # They were testing the harness. This tests the propagation.
        from unittest.mock import patch

        import aictl
        from aictl.sdk import _Response

        for flag in (True, False):
            stub = _Response(text="complaint", model="m", tokens=1,
                             latency_ms=1.0, cost_usd=0.0, cost_jpy=0.0,
                             mock=flag)
            with patch.object(type(aictl.ai), "ask", return_value=stub):
                result = aictl.ai.classify("x", categories=["complaint", "b"])
            self.assertEqual(result.mock, flag)
            self.assertTrue(result.matched)

    def test_repr_shows_the_flags(self):
        from aictl.sdk import Classification

        self.assertIn("UNMATCHED",
                      repr(Classification("a", matched=False)))

    def test_json_serialisation_is_unchanged(self):
        import json

        from aictl.sdk import Classification

        self.assertEqual(json.dumps({"c": Classification("positive")}),
                         '{"c": "positive"}')


class TestTheExamplesRunAsDocumented(unittest.TestCase):
    """They are shipped, copyable, and none of them could be run."""

    def _run(self, path: Path):
        import os
        import tempfile

        env = dict(os.environ)
        env.pop("PYTHONPATH", None)          # the whole point: no help
        env["AIOS_STATE_DIR"] = tempfile.mkdtemp(prefix="aictl-ex-")
        return subprocess.run([sys.executable, str(path)],
                              capture_output=True, text=True, timeout=180,
                              cwd=str(Path(__file__).resolve().parent.parent),
                              env=env)

    def test_there_are_examples_to_check(self):
        self.assertGreaterEqual(len(EXAMPLES), 5)

    def test_every_example_imports_aictl_without_help(self):
        # The README's install is `git clone` with no pip install, so a bare
        # `python3 examples/sdk/x.py` must work from a clone.
        for path in EXAMPLES:
            result = self._run(path)
            self.assertNotIn("ModuleNotFoundError", result.stderr,
                             f"{path.name} cannot import aictl from a clone")

    def test_examples_without_prerequisites_succeed(self):
        for path in EXAMPLES:
            if path.name in NEEDS_ENGINE:
                continue
            result = self._run(path)
            self.assertEqual(result.returncode, 0,
                             f"{path.name} failed: {result.stderr[-300:]}")

    def test_an_example_needing_an_engine_says_so(self):
        for name in NEEDS_ENGINE:
            text = (Path("examples/sdk") / name).read_text()
            self.assertIn("Needs a real model", text,
                          f"{name} fails without an engine but does not say so")

    def test_status_is_used_as_a_property(self):
        # `aictl.ai.status()` raised TypeError: 'dict' object is not callable.
        # docs/SDK.md documents it correctly; only the example was wrong.
        self.assertNotIn("aictl.ai.status()",
                         (Path("examples/sdk") / "05_cost.py").read_text())


class TestStructuredNamesTheRealCause(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext

        _AmbientContext.reset_for_testing()

    def test_the_mock_failure_names_the_mock(self):
        # "Model did not return valid JSON" is true and misleading: it sends a
        # reader looking for a schema or prompt bug when no engine is running.
        import aictl

        if not aictl.ai.status.get("mock"):
            self.skipTest("a real engine is reachable")
        with self.assertRaises(ValueError) as caught:
            aictl.ai.structured("extract", {"type": "object"})
        self.assertIn("mock", str(caught.exception).lower())

    def test_the_message_says_what_to_do(self):
        import aictl

        if not aictl.ai.status.get("mock"):
            self.skipTest("a real engine is reachable")
        with self.assertRaises(ValueError) as caught:
            aictl.ai.structured("extract", {"type": "object"})
        self.assertIn("engine", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
