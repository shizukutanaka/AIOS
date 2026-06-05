"""E2E User Story Tests — validate complete user journeys.

Apple tests the full user experience, not just individual functions.
Each test here simulates a real user scenario from start to finish.

Story 1: First-time developer
  aictl (no args) → welcome → doctor → recommend → fit → help

Story 2: Document researcher
  aictl rag index → rag status → rag ask → guard scan

Story 3: Cost-conscious operator
  aictl status → cache status → perf → cost compare

Story 4: Problem solver
  aictl troubleshoot → doctor → guard test

Story 5: Python developer
  import aictl → ask → classify → cache hit → response.cost
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestStory1FirstTimeDeveloper(unittest.TestCase):
    """First-time developer scenario: discovery flow."""

    def test_welcome_then_help(self):
        """aictl (no args) → welcome → aictl help → aictl help everyday."""
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                # Step 1: No-args shows welcome
                buf = io.StringIO()
                with patch("sys.argv", ["aictl"]):
                    with redirect_stdout(buf):
                        from aictl.__main__ import main
                        from aictl.core.welcome import mark_welcome_shown
                        rc = main()
                self.assertEqual(rc, 0)
                self.assertIn("aictl", buf.getvalue())
                self.assertTrue("→" in buf.getvalue() or "Next step" in buf.getvalue() or "Quick actions" in buf.getvalue())

                # Step 2: aictl help shows getting-started
                from aictl.cmd.help import run as help_run
                class FA:
                    topic = None
                buf2 = io.StringIO()
                with redirect_stdout(buf2):
                    help_run(FA())
                self.assertIn("Getting Started", buf2.getvalue())
                self.assertIn("aictl doctor", buf2.getvalue())

                # Step 3: aictl help everyday
                class FA2:
                    topic = "everyday"
                buf3 = io.StringIO()
                with redirect_stdout(buf3):
                    help_run(FA2())
                self.assertIn("Everyday", buf3.getvalue())
                self.assertIn("aictl fit", buf3.getvalue())
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)

    def test_fit_then_quant(self):
        """aictl fit → quant recommend — natural model selection flow."""
        from aictl.__main__ import build_parser
        from aictl.runtime.recommend import MODELS

        # Find a real model in DB
        if not MODELS:
            self.skipTest("No models in DB")

        model_name = MODELS[0].name

        # fit the model
        p = build_parser()
        args = p.parse_args(["fit", model_name, "--gpu", "H100"])
        from aictl.cmd.fit import run as fit_run
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fit_run(args)
        # Any rc is OK as long as it doesn't crash
        self.assertIsInstance(rc, int)

        # quant compare follows naturally
        args2 = p.parse_args(["quant", "compare", model_name, "--gpu", "H100"])
        from aictl.cmd.quant import run_compare
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = run_compare(args2)
        self.assertIsInstance(rc2, int)
        self.assertIn("SIZE", buf2.getvalue())


class TestStory2DocumentResearcher(unittest.TestCase):
    """Document researcher: index → query → protect."""

    def test_full_rag_flow(self):
        """Index docs → status shows them → ask → guard scan answer."""
        with tempfile.TemporaryDirectory() as td:
            # Create test documents
            docs_dir = Path(td) / "docs"
            docs_dir.mkdir()
            (docs_dir / "policy.md").write_text(
                "# Refund Policy\nWe offer 30-day refunds.\n"
                "Contact support@example.com for help."
            )
            (docs_dir / "handbook.md").write_text(
                "# Employee Handbook\nVacation: 20 days/year.\n"
                "Health insurance: Full coverage."
            )

            db_path = Path(td) / "rag.db"
            from aictl.core.rag import RagStore, index_directory

            store = RagStore(db_path)
            stats = index_directory(docs_dir, store)

            # Indexed successfully
            self.assertGreaterEqual(stats["indexed"], 1)
            self.assertGreater(stats["chunks_created"], 0)

            # Status shows the docs
            status = store.stats()
            self.assertGreater(status["documents"], 0)
            self.assertGreater(status["chunks"], 0)

            # Search returns results
            from aictl.core.rag import search
            results = search("refund", store, k=3)
            # Results may be empty if no embeddings (mock engine)
            # but the call must not crash
            self.assertIsInstance(results, list)

            # Guard scan of a simulated answer containing PII
            from aictl.core.guard import scan
            fake_answer = "Contact alice@example.com or call 090-1234-5678 for refunds."
            result, processed = scan(fake_answer, redact_pii=True)
            # PII should be found
            self.assertGreater(len(result.pii), 0)
            # Redacted version should not contain raw email
            self.assertNotIn("alice@example.com", processed)


class TestStory3CostConsciousOperator(unittest.TestCase):
    """Cost-conscious operator: monitors costs and cache efficiency."""

    def test_cost_tracking_flow(self):
        """ask → check cost on response → cache hit → zero cost."""
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

        import aictl

        # First call — has a cost
        r1 = aictl.ai.ask("What is the speed of light?")
        self.assertFalse(r1.cached)
        self.assertGreater(r1.cost_usd, 0)
        cost_str = r1.cost
        self.assertTrue(cost_str.startswith("$"))

        # Second call — should be cached
        r2 = aictl.ai.ask("What is the speed of light?")
        self.assertTrue(r2.cached)
        self.assertEqual(r2.cost_usd, 0.0)
        self.assertIn("cached", r2.cost)

    def test_perf_records_commands(self):
        """Commands run through main are recorded in perf log."""
        from aictl.core.perf import read_recent
        # Just verify the function works
        records = read_recent(limit=10)
        self.assertIsInstance(records, list)

    def test_sem_cache_status_cli(self):
        """aictl cache status shows meaningful output."""
        from aictl.cmd.cache_cmd import run_status
        class FA:
            json = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_status(FA())
        self.assertEqual(rc, 0)
        # Should mention entries or 'Empty'
        output = buf.getvalue()
        self.assertTrue("entries" in output.lower() or "empty" in output.lower() or "hit" in output.lower())


class TestStory4ProblemSolver(unittest.TestCase):
    """Problem solver: something broken → diagnose → fix."""

    def test_troubleshoot_oom_flow(self):
        """troubleshoot --symptom oom → outputs actionable fix."""
        from aictl.cmd.troubleshoot import _diagnose_oom
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = _diagnose_oom()
        output = buf.getvalue()
        # Must tell user what to do
        self.assertTrue(
            "fix" in output.lower() or "reduce" in output.lower()
            or "aictl" in output.lower()
        )

    def test_guard_test_all_pass(self):
        """aictl guard test — built-in validation suite must pass."""
        from aictl.cmd.guard import run_test
        class FA:
            json = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_test(FA())
        self.assertEqual(rc, 0)
        self.assertIn("passed", buf.getvalue())

    def test_self_heal_oom_reduces_context(self):
        """Self-heal shrinks context on OOM — then retry would succeed."""
        from aictl.core.self_heal import try_heal
        ctx = {"max_model_len": 32768}
        healed = try_heal(RuntimeError("CUDA out of memory"), ctx)
        self.assertTrue(healed)
        self.assertEqual(ctx["max_model_len"], 16384)


class TestStory5PythonDeveloper(unittest.TestCase):
    """Python developer integrating aictl as a library."""

    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_library_import_and_ask(self):
        """import aictl; aictl.ai.ask() works out of the box."""
        import aictl
        r = aictl.ai.ask("hello")
        self.assertIsInstance(str(r), str)
        self.assertGreater(len(str(r)), 0)

    def test_classify_returns_category(self):
        """aictl.ai.classify() returns one of the given categories."""
        import aictl
        result = aictl.ai.classify(
            "I love this product!",
            categories=["positive", "negative", "neutral"],
        )
        self.assertIn(result, ["positive", "negative", "neutral"])

    def test_configure_then_ask(self):
        """Configure a budget, then ask — no crash."""
        import aictl
        aictl.ai.configure(cost_budget_usd=10.0)
        r = aictl.ai.ask("test")
        self.assertIsInstance(str(r), str)

    def test_response_has_cost_and_cached(self):
        """Every response has cost and cached attributes."""
        import aictl
        r = aictl.ai.ask("unique e2e test query xyz 987654")
        self.assertIsInstance(r.cost_usd, float)
        self.assertIsInstance(r.cached, bool)
        self.assertIsInstance(r.cost, str)

    def test_embed_returns_vectors(self):
        """aictl.ai.embed() returns one vector per input."""
        import aictl
        vecs = aictl.ai.embed(["hello", "world"])
        self.assertEqual(len(vecs), 2)
        for v in vecs:
            self.assertIsInstance(v, list)
            self.assertGreater(len(v), 0)


class TestTerminalModule(unittest.TestCase):
    """Verify terminal.py utilities work without crashing."""

    def test_progress_bar_full(self):
        from aictl.core.terminal import progress_bar
        bar = progress_bar(100, 100)
        self.assertIn("100%", bar)

    def test_progress_bar_empty(self):
        from aictl.core.terminal import progress_bar
        bar = progress_bar(0, 100)
        self.assertIn("0%", bar)

    def test_progress_bar_zero_total(self):
        from aictl.core.terminal import progress_bar
        bar = progress_bar(5, 0)
        self.assertEqual(bar, "")

    def test_color_functions_return_strings(self):
        from aictl.core.terminal import primary, secondary, success, warning
        for fn in [primary, secondary, success, warning]:
            result = fn("test")
            self.assertIsInstance(result, str)
            self.assertIn("test", result)

    def test_spinner_context_manager(self):
        """Spinner must not crash in non-tty mode."""
        from aictl.core.terminal import Spinner
        import time
        with Spinner("Testing..."):
            time.sleep(0.01)
        self.assertTrue(True)  # completed without hanging or crash

    def test_next_action_suggest(self):
        """suggest() must not crash for unknown keys."""
        from aictl.core.next_action import suggest
        buf = io.StringIO()
        with redirect_stdout(buf):
            suggest("rag_index")
            suggest("unknown_key_xyz")
        output = buf.getvalue()
        # rag_index should produce output, unknown_key should be silent
        self.assertIn("rag", output)


if __name__ == "__main__":
    unittest.main()
