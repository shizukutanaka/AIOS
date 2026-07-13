"""Pass 185 (IMPROVEMENTS.md item A-2, degraded-embedding honesty half):
unify the SDK's embedding fallback with rag's, and flag degraded
embeddings in `cache status` like `rag status` already does.

Two real defects found by reading the actual code paths (not assumed):

1. sdk._embed's per-text fallback emitted 32-dim vectors (one raw sha256
   digest) while core/rag.py's fallback and its "is this semantic?"
   detector both use FALLBACK_DIM (64). Consequence: when the ENGINE is
   reachable but the embedding model isn't pulled (the most common real
   degradation -- Ollama up, nomic-embed-text absent), the SDK silently
   produced 32-dim hash vectors that `rag status` then misreported as
   "semantic + lexical (hybrid)". A false quality claim. Also, a
   partially-failing batch mixed real-model dims with 32-dim hash dims;
   cosine() between mismatched dims returns 0.0, silently zeroing
   similarity for the failed subset. Fix: batch-level fallback in
   sdk._embed reusing rag._fallback_embedding (64-dim) -- vectors are now
   always all-real or all-hash(64), and 64 is the marker the status
   commands detect.

2. `cache status` had no degraded-embeddings flag at all, while
   `rag status` (which shares the same embedding path) has warned since
   the hybrid-retrieval pass. SemanticCache.stats() now returns
   `semantic_embeddings` using the same FALLBACK_DIM-width detection, and
   `aictl cache status` warns that only exact-match hits are reliable
   when the cached vectors are hash-fallback, with the concrete remedy
   (`ollama pull nomic-embed-text`).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestSdkFallbackDimensionUnified(unittest.TestCase):
    def test_unreachable_endpoint_falls_back_to_fallback_dim(self):
        from aictl.sdk import _embed
        from aictl.core.rag import FALLBACK_DIM
        vectors = _embed("http://127.0.0.1:1", ["hello", "world"])
        self.assertEqual([len(v) for v in vectors], [FALLBACK_DIM, FALLBACK_DIM])

    def test_fallback_matches_rag_fallback_exactly(self):
        # Same text -> identical vector from both layers, so downstream
        # cosine similarity between SDK-embedded and rag-embedded copies of
        # the same content behaves consistently in degraded mode.
        from aictl.sdk import _embed
        from aictl.core.rag import _fallback_embedding
        [via_sdk] = _embed("http://127.0.0.1:1", ["same text"])
        self.assertEqual(via_sdk, _fallback_embedding("same text"))

    def test_partial_batch_failure_degrades_whole_batch_uniformly(self):
        # First text succeeds (mocked 768-dim), second raises: the old code
        # returned [768-dim real, 32-dim hash]; now the WHOLE batch degrades
        # to uniform FALLBACK_DIM vectors.
        from aictl.core.rag import FALLBACK_DIM
        from aictl import sdk as sdk_mod

        calls = {"n": 0}

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(
                    {"data": [{"embedding": [0.1] * 768}]}).encode()

        def fake_urlopen(req, timeout=0):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResp()
            raise OSError("model not pulled")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            vectors = sdk_mod._embed("http://fake-endpoint", ["ok", "fails"])

        self.assertEqual(len(vectors), 2)
        self.assertEqual({len(v) for v in vectors}, {FALLBACK_DIM},
                        "a partial failure must degrade the WHOLE batch uniformly")

    def test_all_success_batch_returns_real_vectors(self):
        from aictl import sdk as sdk_mod

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(
                    {"data": [{"embedding": [0.5] * 768}]}).encode()

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            vectors = sdk_mod._embed("http://fake-endpoint", ["a", "b"])
        self.assertEqual([len(v) for v in vectors], [768, 768])


class TestRagStatusNoLongerFooledBySdkFallback(unittest.TestCase):
    def test_sdk_degraded_vectors_detected_as_non_semantic(self):
        # The exact false-success scenario: store chunks whose embeddings
        # came from the SDK's degraded path -- stats must say non-semantic.
        # (Before this pass the SDK fallback was 32-dim, != FALLBACK_DIM, so
        # this very assertion would have failed with semantic=True.)
        from aictl.core.rag import RagStore, Chunk, _doc_id_for
        from aictl.sdk import _embed

        with tempfile.TemporaryDirectory() as tmp:
            store = RagStore(db_path=Path(tmp) / "rag.db")
            [vec] = _embed("http://127.0.0.1:1", ["some indexed content"])
            doc_id = _doc_id_for("doc.md")
            store.upsert_doc("doc.md", mtime=1.0, size=100, chunks=[
                Chunk(doc_id=doc_id, chunk_idx=0, source="doc.md",
                      text="some indexed content", embedding=vec),
            ])
            status = store.stats()
            self.assertFalse(
                status["semantic_embeddings"],
                "SDK-degraded (hash) vectors must be reported non-semantic")


class TestCacheStatusSemanticFlag(unittest.TestCase):
    def _cache(self, tmp):
        from aictl.core.sem_cache import SemanticCache
        return SemanticCache(db_path=Path(tmp) / "c.db")

    def test_empty_cache_reports_non_semantic(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = self._cache(tmp).stats()
            self.assertIn("semantic_embeddings", stats)
            self.assertFalse(stats["semantic_embeddings"])

    def test_fallback_dim_entries_report_non_semantic(self):
        from aictl.core.rag import FALLBACK_DIM
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            with patch("aictl.core.rag.embed_text",
                      return_value=[[0.1] * FALLBACK_DIM]):
                cache.store("prompt", "response", "model-x", tokens=10)
            self.assertFalse(cache.stats()["semantic_embeddings"])

    def test_real_dim_entries_report_semantic(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            with patch("aictl.core.rag.embed_text",
                      return_value=[[0.1] * 768]):
                cache.store("prompt", "response", "model-x", tokens=10)
            self.assertTrue(cache.stats()["semantic_embeddings"])

    def test_cache_status_cli_warns_on_fallback_embeddings(self):
        # warn() writes to STDERR (core/output.py) -- capture both streams.
        import argparse
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from aictl.core.rag import FALLBACK_DIM

        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            with patch("aictl.core.rag.embed_text",
                      return_value=[[0.1] * FALLBACK_DIM]):
                cache.store("prompt", "response", "model-x", tokens=10)

            from aictl.cmd import cache_cmd
            out, errbuf = io.StringIO(), io.StringIO()
            with patch("aictl.core.sem_cache.get_default_cache", return_value=cache), \
                 redirect_stdout(out), redirect_stderr(errbuf):
                cache_cmd.run_status(argparse.Namespace(json=False))
            combined = out.getvalue() + errbuf.getvalue()
            self.assertIn("hash fallback", combined)
            self.assertIn("nomic-embed-text", combined)

    def test_cache_status_cli_no_warning_when_semantic(self):
        import argparse
        import io
        from contextlib import redirect_stdout, redirect_stderr

        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            with patch("aictl.core.rag.embed_text",
                      return_value=[[0.1] * 768]):
                cache.store("prompt", "response", "model-x", tokens=10)

            from aictl.cmd import cache_cmd
            out, errbuf = io.StringIO(), io.StringIO()
            with patch("aictl.core.sem_cache.get_default_cache", return_value=cache), \
                 redirect_stdout(out), redirect_stderr(errbuf):
                cache_cmd.run_status(argparse.Namespace(json=False))
            self.assertNotIn("hash fallback", out.getvalue() + errbuf.getvalue())


if __name__ == "__main__":
    unittest.main()
