"""Tests for RAG and prefix-cache aware routing."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestRAGStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_store_stats(self):
        from aictl.core.rag import RagStore
        store = RagStore(self.db_path)
        stats = store.stats()
        self.assertEqual(stats["documents"], 0)
        self.assertEqual(stats["chunks"], 0)

    def test_upsert_and_retrieve(self):
        from aictl.core.rag import RagStore, Chunk, _doc_id_for
        store = RagStore(self.db_path)
        source = "/fake/path/doc.md"
        doc_id = _doc_id_for(source)
        chunks = [
            Chunk(doc_id=doc_id, chunk_idx=0, source=source,
                  text="hello world", embedding=[0.1, 0.2, 0.3]),
            Chunk(doc_id=doc_id, chunk_idx=1, source=source,
                  text="goodbye world", embedding=[0.4, 0.5, 0.6]),
        ]
        store.upsert_doc(source, mtime=1000.0, size=100, chunks=chunks)
        stats = store.stats()
        self.assertEqual(stats["documents"], 1)
        self.assertEqual(stats["chunks"], 2)
        self.assertEqual(stats["embedded"], 2)

        retrieved = list(store.all_chunks_with_embeddings())
        self.assertEqual(len(retrieved), 2)

    def test_upsert_replaces_old(self):
        from aictl.core.rag import RagStore, Chunk, _doc_id_for
        store = RagStore(self.db_path)
        source = "/fake/doc.md"
        doc_id = _doc_id_for(source)
        # First version: 3 chunks
        chunks_v1 = [
            Chunk(doc_id=doc_id, chunk_idx=i, source=source,
                  text=f"v1-{i}", embedding=[0.1] * 3)
            for i in range(3)
        ]
        store.upsert_doc(source, mtime=1.0, size=10, chunks=chunks_v1)
        self.assertEqual(store.stats()["chunks"], 3)
        # Second version: 1 chunk
        chunks_v2 = [
            Chunk(doc_id=doc_id, chunk_idx=0, source=source,
                  text="v2", embedding=[0.2] * 3),
        ]
        store.upsert_doc(source, mtime=2.0, size=20, chunks=chunks_v2)
        self.assertEqual(store.stats()["chunks"], 1)

    def test_needs_reindex(self):
        from aictl.core.rag import RagStore, Chunk, _doc_id_for
        store = RagStore(self.db_path)
        source = "/foo/bar.md"
        # New file → needs index
        self.assertTrue(store.needs_reindex(source, 1.0, 100))
        # After indexing
        chunks = [Chunk(_doc_id_for(source), 0, source, "x", [0.0])]
        store.upsert_doc(source, 1.0, 100, chunks)
        # Same mtime+size → no reindex
        self.assertFalse(store.needs_reindex(source, 1.0, 100))
        # Newer mtime → reindex
        self.assertTrue(store.needs_reindex(source, 2.0, 100))
        # Different size → reindex
        self.assertTrue(store.needs_reindex(source, 1.0, 200))

    def test_rag_store_clear_removes_all_data(self):
        from aictl.core.rag import RagStore, Chunk, _doc_id_for
        store = RagStore(self.db_path)
        chunks = [Chunk(_doc_id_for("/x"), 0, "/x", "hi", [0.0])]
        store.upsert_doc("/x", 1.0, 1, chunks)
        store.clear()
        self.assertEqual(store.stats()["chunks"], 0)


class TestRAGChunking(unittest.TestCase):
    def test_short_text_one_chunk(self):
        from aictl.core.rag import chunk_text
        chunks = chunk_text("Hello world.")
        self.assertEqual(len(chunks), 1)

    def test_empty_text_no_chunks(self):
        from aictl.core.rag import chunk_text
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   \n   "), [])

    def test_long_text_splits_into_multiple(self):
        from aictl.core.rag import chunk_text
        # 10000 chars without paragraph breaks → must split
        text = "x" * 10000
        chunks = chunk_text(text, chunk_size=1000, overlap=100)
        self.assertGreater(len(chunks), 1)

    def test_paragraphs_kept_together_when_possible(self):
        from aictl.core.rag import chunk_text
        text = "Para A.\n\nPara B.\n\nPara C."
        chunks = chunk_text(text, chunk_size=10000, overlap=10)
        self.assertEqual(len(chunks), 1)
        self.assertIn("Para A", chunks[0])
        self.assertIn("Para C", chunks[0])


class TestRAGEmbedding(unittest.TestCase):
    def test_fallback_embedding_deterministic(self):
        from aictl.core.rag import _fallback_embedding
        a = _fallback_embedding("hello")
        b = _fallback_embedding("hello")
        self.assertEqual(a, b)

    def test_fallback_embedding_different_for_different_inputs(self):
        from aictl.core.rag import _fallback_embedding
        a = _fallback_embedding("hello")
        b = _fallback_embedding("world")
        self.assertNotEqual(a, b)

    def test_fallback_embedding_dimension(self):
        from aictl.core.rag import _fallback_embedding
        v = _fallback_embedding("test", dim=128)
        self.assertEqual(len(v), 128)


class TestRAGSimilarity(unittest.TestCase):
    def test_cosine_identical(self):
        from aictl.core.rag import cosine
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(cosine(v, v), 1.0)

    def test_cosine_orthogonal(self):
        from aictl.core.rag import cosine
        self.assertAlmostEqual(cosine([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_cosine_empty_vectors(self):
        from aictl.core.rag import cosine
        self.assertEqual(cosine([], [1.0]), 0.0)
        self.assertEqual(cosine([0.0, 0.0], [1.0, 1.0]), 0.0)


class TestRAGIndexDirectory(unittest.TestCase):
    def test_index_creates_records(self):
        from aictl.core.rag import RagStore, index_directory
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a.md").write_text("# A\n\nContent of A.")
            (Path(td) / "b.txt").write_text("Content of B.")
            (Path(td) / "skip.png").write_bytes(b"\x89PNG fake")

            db_path = Path(td) / "rag.db"
            store = RagStore(db_path)
            stats = index_directory(Path(td), store)

            # 2 indexed (a.md, b.txt), 1 skipped (the png is binary, plus the db itself)
            self.assertGreaterEqual(stats["indexed"], 2)
            self.assertGreater(stats["chunks_created"], 0)


class TestPrefixRoute(unittest.TestCase):
    def setUp(self):
        from aictl.runtime.prefix_route import PrefixRouteTracker
        self.tracker = PrefixRouteTracker()

    def test_no_history_returns_none(self):
        match = self.tracker.best_endpoint("hello", ["http://a", "http://b"])
        self.assertIsNone(match)

    def test_record_then_match(self):
        prompt = "You are a helpful assistant. " * 50
        self.tracker.record("http://a", prompt)
        match = self.tracker.best_endpoint(prompt, ["http://a", "http://b"])
        self.assertIsNotNone(match)
        self.assertEqual(match.endpoint, "http://a")
        self.assertGreater(match.overlap_score, 0)

    def test_partial_prefix_matches(self):
        # Record a short prompt at endpoint a
        shared_prefix = "You are a helpful assistant. Please answer: "
        short_prompt = shared_prefix + "short question"
        long_prompt = shared_prefix + "long question with more words"
        # Record the short prompt
        self.tracker.record("http://a", short_prompt)
        # Query with the long prompt — they share the same prefix chars
        match = self.tracker.best_endpoint(
            long_prompt, ["http://a", "http://b"]
        )
        # Should match on the shared prefix characters
        self.assertIsNotNone(match)
        self.assertEqual(match.endpoint, "http://a")

    def test_unrelated_prompts_no_match(self):
        self.tracker.record("http://a", "completely unrelated content here")
        match = self.tracker.best_endpoint(
            "totally different prompt with nothing in common",
            ["http://a", "http://b"],
        )
        self.assertIsNone(match)

    def test_lru_capacity(self):
        from aictl.runtime.prefix_route import PrefixRouteTracker
        small = PrefixRouteTracker(capacity=3)
        # Record 5 distinct prompts
        for i in range(5):
            small.record("http://a", f"prompt number {i} " * 100)
        stats = small.stats()
        self.assertLessEqual(
            stats["totals"]["http://a"]["tracked_prefixes"], 3
        )

    def test_stats_format(self):
        self.tracker.record("http://a", "p1 " * 200)
        self.tracker.record("http://b", "p2 " * 200)
        stats = self.tracker.stats()
        self.assertIn("endpoints", stats)
        self.assertIn("totals", stats)
        self.assertEqual(set(stats["endpoints"]), {"http://a", "http://b"})

    def test_prefix_tracker_clear_resets_state(self):
        self.tracker.record("http://a", "test " * 100)
        self.tracker.clear()
        self.assertEqual(self.tracker.stats()["endpoints"], [])

    def test_thread_safety_basic(self):
        """Concurrent record and best_endpoint must not crash."""
        import threading
        finished = [False]

        def worker():
            for i in range(100):
                self.tracker.record(f"http://e{i % 3}", f"prompt {i} " * 50)
                self.tracker.best_endpoint(
                    f"prompt {i} " * 50,
                    ["http://e0", "http://e1", "http://e2"],
                )
            finished[0] = True

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(finished[0])


class TestRagCli(unittest.TestCase):
    def test_index_subcommand_parses(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["rag", "index", "/some/path"])
        self.assertEqual(args.path, "/some/path")

    def test_ask_subcommand_parses(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["rag", "ask", "What is X?", "-k", "10"])
        self.assertEqual(args.question, "What is X?")
        self.assertEqual(args.k, 10)

    def test_status_subcommand_parses(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["rag", "status"])
        # Should not raise
        self.assertEqual(args.rag_cmd, 'status')

    def test_reset_requires_yes(self):
        from aictl.__main__ import build_parser
        from aictl.cmd.rag import run_reset
        p = build_parser()
        # Without --yes, should refuse
        args = p.parse_args(["rag", "reset"])
        rc = run_reset(args)
        self.assertNotEqual(rc, 0)


class TestHybridRetrieval(unittest.TestCase):
    """Dense+lexical (BM25/RRF) hybrid retrieval and degraded-mode signalling."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "rag.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _index(self, texts):
        from aictl.core.rag import RagStore, Chunk, _doc_id_for, _fallback_embedding
        store = RagStore(self.db_path)
        source = "/fake/doc.md"
        doc_id = _doc_id_for(source)
        chunks = [
            Chunk(doc_id=doc_id, chunk_idx=i, source=source,
                  text=t, embedding=_fallback_embedding(t))
            for i, t in enumerate(texts)
        ]
        store.upsert_doc(source, mtime=1.0, size=100, chunks=chunks)
        return store

    def test_bm25_ranks_lexical_match_first(self):
        from aictl.core.rag import Chunk, bm25_rank
        chunks = [
            Chunk("d", 0, "s", "the quick brown fox jumps"),
            Chunk("d", 1, "s", "kubernetes autoscaling with KEDA and HPA"),
            Chunk("d", 2, "s", "a lazy dog sleeps all day"),
        ]
        ranked = bm25_rank("kubernetes autoscaling", chunks)
        self.assertTrue(ranked, "BM25 should return a match")
        # The chunk that shares both query terms must rank first.
        self.assertEqual(ranked[0][1].chunk_idx, 1)

    def test_bm25_no_shared_terms_returns_empty(self):
        from aictl.core.rag import Chunk, bm25_rank
        chunks = [Chunk("d", 0, "s", "alpha beta gamma")]
        self.assertEqual(bm25_rank("zzz qqq", chunks), [])

    def test_rrf_rewards_agreement(self):
        from aictl.core.rag import Chunk, reciprocal_rank_fusion
        a = Chunk("d", 0, "s", "a")
        b = Chunk("d", 1, "s", "b")
        # 'a' is rank0 in one list, rank0 in the other → highest fused score.
        r1 = [((a.doc_id, 0), a), ((b.doc_id, 1), b)]
        r2 = [((a.doc_id, 0), a), ((b.doc_id, 1), b)]
        fused = reciprocal_rank_fusion([r1, r2])
        self.assertEqual(fused[0][0].chunk_idx, 0)
        self.assertGreater(fused[0][1], fused[1][1])

    def test_hybrid_search_finds_lexical_match_with_fallback_embeddings(self):
        # The core fix: with non-semantic fallback embeddings, lexical BM25 must
        # still surface the relevant chunk — dense-only cosine on hash vectors
        # would rank near-randomly.
        from aictl.core.rag import search
        store = self._index([
            "general notes about cooking pasta and sauce",
            "the refund policy allows returns within thirty days",
            "weather patterns over the pacific ocean",
        ])
        matches = search("refund policy returns", store, k=3)
        self.assertTrue(matches)
        self.assertIn("refund policy", matches[0][0].text)

    def test_empty_query_returns_nothing(self):
        from aictl.core.rag import search
        store = self._index(["something"])
        self.assertEqual(search("   ", store), [])

    def test_stats_flags_fallback_embeddings(self):
        # 64-dim fallback vectors → degraded; wider vectors → semantic.
        store = self._index(["hello world"])
        self.assertFalse(store.stats()["semantic_embeddings"])

        from aictl.core.rag import RagStore, Chunk, _doc_id_for
        store2 = RagStore(Path(self.tmp.name) / "rag2.db")
        src = "/fake/real.md"
        store2.upsert_doc(src, 1.0, 10, [
            Chunk(_doc_id_for(src), 0, src, "real", embedding=[0.01] * 384),
        ])
        self.assertTrue(store2.stats()["semantic_embeddings"])


if __name__ == "__main__":
    unittest.main()
