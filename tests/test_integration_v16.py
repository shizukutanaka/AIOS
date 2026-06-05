"""Tests for the new integration features: cache, dash, prefix routing."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestCacheCommand(unittest.TestCase):
    def test_cache_status_parses(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["cache", "status"])
        self.assertEqual(args.cache_cmd, "status")

    def test_cache_clear_requires_yes(self):
        from aictl.cmd.cache_cmd import run_clear

        class FakeArgs:
            yes = False
            json = False
        rc = run_clear(FakeArgs())
        self.assertNotEqual(rc, 0)

    def test_cache_clear_with_yes(self):
        from aictl.cmd.cache_cmd import run_clear

        class FakeArgs:
            yes = True
            json = False
        rc = run_clear(FakeArgs())
        self.assertEqual(rc, 0)

    def test_cache_status_empty(self):
        from aictl.core.sem_cache import SemanticCache

        with tempfile.TemporaryDirectory() as td:
            import aictl.core.sem_cache as _mod
            orig = _mod._DEFAULT_CACHE
            _mod._DEFAULT_CACHE = SemanticCache(Path(td) / "test.db")
            try:
                from aictl.cmd.cache_cmd import run_status
                import io
                from contextlib import redirect_stdout

                class FakeArgs:
                    json = False

                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = run_status(FakeArgs())
                self.assertEqual(rc, 0)
                self.assertIn("Empty", buf.getvalue())
            finally:
                _mod._DEFAULT_CACHE = orig


class TestDashCommand(unittest.TestCase):
    def test_dash_parses(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["dash"])
        self.assertFalse(args.watch)

    def test_dash_watch_flag(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["dash", "--watch", "--interval", "2.0"])
        self.assertTrue(args.watch)
        self.assertEqual(args.interval, 2.0)

    def test_dash_renders_without_crash(self):
        """Rendering should not raise even with no engines online."""
        import io
        from contextlib import redirect_stdout
        from aictl.cmd.dash import _render

        buf = io.StringIO()
        with redirect_stdout(buf):
            _render()  # Must not raise

        output = buf.getvalue()
        # All sections should appear
        self.assertIn("System", output)
        self.assertIn("Engines", output)
        self.assertIn("Cache", output)
        self.assertIn("Performance", output)
        self.assertIn("Guardrails", output)
        self.assertIn("RAG", output)


class TestPrefixRouteInRouter(unittest.TestCase):
    """Verify prefix_route integration doesn't crash the router."""

    def test_soft_score_with_prefix_tracker(self):
        """Router._soft_score must not raise even with prefix_route installed."""
        from aictl.runtime.router import BrokerRouter, RouteRequest
        from aictl.runtime.prefix_route import get_default_tracker

        tracker = get_default_tracker()
        tracker.record("http://fake:8000", "qwen3:7b")

        router = BrokerRouter(endpoints={"vllm": "http://fake:8000"})
        req = RouteRequest(model="qwen3:7b", objective="balanced")
        decision = router.route(req)
        self.assertIsNotNone(decision)


class TestSemanticCacheInSDK(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_second_identical_ask_is_cached(self):
        import aictl
        r1 = aictl.ai.ask("What is the capital of France?")
        self.assertFalse(r1.cached)

        r2 = aictl.ai.ask("What is the capital of France?")
        self.assertTrue(r2.cached)
        self.assertEqual(r2.cost_usd, 0.0)

    def test_cached_response_has_zero_cost(self):
        import aictl
        aictl.ai.ask("Test prompt for cost check")
        r = aictl.ai.ask("Test prompt for cost check")
        self.assertTrue(r.cached)
        self.assertEqual(r.cost, "$0.000000 (cached)")

    def test_response_cost_property_format(self):
        import aictl
        r = aictl.ai.ask("Unique prompt xyz 12345 abc")
        # Not cached — should have some cost
        self.assertFalse(r.cached)
        cost_str = r.cost
        self.assertTrue(cost_str.startswith("$"))


if __name__ == "__main__":
    unittest.main()
