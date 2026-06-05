"""Regression tests for v1.6.0 correctness fixes.

Each test pins a specific bug fixed in the deep-research improvement pass:
- rag.chunk_text: must not crash / silently drop on overlap >= chunk_size
- SemanticCache.stats: must expose the DB lifetime tokens-saved total
- BrokerRouter.route: fallback_used reflects whether the fallback path ran
- route._explain_score: keyword labels must not be mangled by \\b stripping
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestChunkTextOverlapGuard(unittest.TestCase):
    """rag.chunk_text must tolerate overlap >= chunk_size (was a crash / silent drop)."""

    def test_overlap_equal_to_chunk_size_does_not_raise(self):
        from aictl.core.rag import chunk_text
        text = "word " * 500  # ~2500 chars, single paragraph
        # step = chunk_size - overlap = 0 used to raise "range() arg 3 must not be zero"
        chunks = chunk_text(text, chunk_size=100, overlap=100)
        self.assertTrue(chunks, "expected non-empty chunks, got none")
        self.assertTrue(all(c for c in chunks))

    def test_overlap_greater_than_chunk_size_still_chunks(self):
        from aictl.core.rag import chunk_text
        text = "x" * 1000  # one long paragraph, no boundaries
        # step would go negative -> empty range -> paragraph silently dropped
        chunks = chunk_text(text, chunk_size=50, overlap=200)
        self.assertTrue(chunks, "oversized paragraph was silently dropped")
        joined = "".join(chunks)
        self.assertGreaterEqual(len(joined), len(text))

    def test_zero_chunk_size_is_clamped(self):
        from aictl.core.rag import chunk_text
        chunks = chunk_text("hello world " * 50, chunk_size=0, overlap=0)
        self.assertTrue(chunks)


class TestSemCacheLifetimeStats(unittest.TestCase):
    """SemanticCache.stats must report the DB lifetime tokens-saved, not just the session value."""

    def test_lifetime_tokens_saved_reflects_db(self):
        from aictl.core.sem_cache import SemanticCache
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "sem_cache.db"
            cache = SemanticCache(db_path=db)
            emb = [0.1] * 64
            cache.store("prompt one", "answer one", "mock", tokens=100, embedding=emb)
            cache.store("prompt two", "answer two", "mock", tokens=50, embedding=emb)

            # Simulate prior-process hits: 3 hits on the 100-token entry, 0 on the other.
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "UPDATE cache SET hits = 3 WHERE tokens_saved = 100"
                )

            stats = cache.stats()
            # Fresh instance has recorded no in-process hits...
            self.assertEqual(stats["total_tokens_saved"], 0)
            # ...but the lifetime total must come from the DB: 100 * 3 = 300.
            self.assertIn("lifetime_tokens_saved", stats)
            self.assertEqual(stats["lifetime_tokens_saved"], 300)


class TestRouterFallbackFlag(unittest.TestCase):
    """fallback_used must be False on direct selection, True only when the fallback path runs."""

    def _route_with_health(self, **health_kwargs):
        from aictl.runtime import router as router_mod
        from aictl.runtime.router import BrokerRouter, RouteRequest
        from aictl.runtime.adapters import EngineHealth

        health = EngineHealth(engine="vllm", endpoint="http://fake:8000", **health_kwargs)
        orig = router_mod.discover_engines
        router_mod.discover_engines = lambda endpoints: [health]
        try:
            router = BrokerRouter(endpoints={"vllm": "http://fake:8000"})
            return router.route(RouteRequest(model="qwen3:7b", objective="balanced"))
        finally:
            router_mod.discover_engines = orig

    def test_direct_selection_is_not_fallback(self):
        decision = self._route_with_health(reachable=True, status="READY")
        self.assertEqual(decision.selected_engine, "vllm")
        self.assertFalse(decision.fallback_used)

    def test_reachable_but_not_ready_uses_fallback(self):
        # Rejected by the hard filter (status != READY/DEGRADED) yet reachable,
        # so the priority-order fallback selects it and sets fallback_used.
        decision = self._route_with_health(reachable=True, status="WARMING")
        self.assertEqual(decision.selected_engine, "vllm")
        self.assertTrue(decision.fallback_used)


class TestRouteExplainKeyword(unittest.TestCase):
    """route._explain_score must show clean keywords, not \\b-mangled fragments."""

    def test_keyword_label_is_clean(self):
        from aictl.cmd.route import _explain_score
        reasons = _explain_score("Can you explain why this works?")
        kw_reasons = [r for r in reasons if r.startswith("Complex keyword:")]
        self.assertTrue(kw_reasons)
        for r in kw_reasons:
            self.assertNotIn("\\", r)
            self.assertNotIn("\\b", r)
        # 'explain' must survive intact (no leading/trailing char loss).
        self.assertTrue(any("explain" in r for r in kw_reasons))


if __name__ == "__main__":
    unittest.main()
