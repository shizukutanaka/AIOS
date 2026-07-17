"""Pass 189 (IMPROVEMENTS.md item A-3): pluggable reranker for RAG search
results.

Design (from a research+design Workflow): implement a single TEI
(HuggingFace Text Embeddings Inference) -compatible /rerank contract only.
Research found Ollama has no native rerank endpoint at all, and while vLLM
claims Cohere-compatibility for its own /rerank, its exact field names
could not be independently confirmed (vLLM's own docs 403'd automated
fetches three times) -- TEI's contract is the only one verified against its
own OpenAPI spec, so it's the one shipped rather than guessing at an
unverified shape.

core.rerank.rerank(endpoint, model, query, candidates) reranks a widened
RRF-fused candidate pool (max(k*4, RERANK_POOL_MIN)) before the final
top-K slice in core.rag.search()/answer(). Off by default
(Config.rerank_endpoint == "" -- zero network calls, RRF order unchanged);
any failure at any step (unreachable endpoint, non-2xx, malformed JSON,
out-of-range index) silently abstains to the pre-existing RRF order.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from aictl.core.config import Config
from aictl.core.rag import Chunk, RagStore, search
from aictl.core.rerank import rerank


def _rerank_server(handler_fn):
    """Local HTTP server for a single /rerank POST, driven by handler_fn(body) -> (status, json_body|None)."""
    calls = {"n": 0, "last_body": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls["n"] += 1
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw)
            except Exception:
                body = {}
            calls["last_body"] = body
            status, payload = handler_fn(body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if payload is not None:
                self.wfile.write(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, calls, f"http://127.0.0.1:{port}"


def _make_store(tmp_dir, texts_and_vecs):
    db_path = Path(tmp_dir) / "rag.db"
    store = RagStore(db_path)
    doc_id = "doc1"
    chunks = [
        Chunk(doc_id=doc_id, chunk_idx=i, source="doc.md", text=text, embedding=vec)
        for i, (text, vec) in enumerate(texts_and_vecs)
    ]
    store.upsert_doc("doc.md", mtime=1.0, size=100, chunks=chunks)
    return store


class TestRerankModuleDefaults(unittest.TestCase):
    def test_empty_endpoint_returns_none(self):
        self.assertIsNone(rerank("", "model", "q", [(Chunk("d", 0, "s", "t"), 1.0)]))

    def test_empty_candidates_returns_none(self):
        self.assertIsNone(rerank("http://x", "model", "q", []))

    def test_non_http_scheme_rejected(self):
        self.assertIsNone(rerank("file:///etc/passwd", "model", "q",
                                 [(Chunk("d", 0, "s", "t"), 1.0)]))

    def test_unreachable_endpoint_returns_none_never_raises(self):
        result = rerank("http://127.0.0.1:1", "model", "q",
                        [(Chunk("d", 0, "s", "t"), 1.0)])
        self.assertIsNone(result)


class TestRerankModuleRealServer(unittest.TestCase):
    def test_reverses_order_correctly(self):
        candidates = [
            (Chunk("d", 0, "s", "first"), 0.9),
            (Chunk("d", 1, "s", "second"), 0.8),
            (Chunk("d", 2, "s", "third"), 0.7),
        ]

        def handler(body):
            n = len(body["texts"])
            return 200, [{"index": n - 1 - i, "score": 1.0 - i * 0.1} for i in range(n)]

        server, thread, calls, url = _rerank_server(handler)
        try:
            result = rerank(url, "bge-reranker-v2-m3", "q", candidates)
            self.assertIsNotNone(result)
            self.assertEqual([c.text for c, _ in result], ["third", "second", "first"])
            self.assertEqual(calls["last_body"]["query"], "q")
            self.assertEqual(calls["last_body"]["texts"], ["first", "second", "third"])
            self.assertEqual(calls["last_body"]["model"], "bge-reranker-v2-m3")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_model_field_omitted_when_empty(self):
        candidates = [(Chunk("d", 0, "s", "a"), 1.0)]

        def handler(body):
            return 200, [{"index": 0, "score": 0.5}]

        server, thread, calls, url = _rerank_server(handler)
        try:
            rerank(url, "", "q", candidates)
            self.assertNotIn("model", calls["last_body"])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_malformed_json_returns_none(self):
        server, thread, calls, url = _rerank_server(lambda body: (200, b"not json"))
        try:
            result = rerank(url, "m", "q", [(Chunk("d", 0, "s", "a"), 1.0)])
            self.assertIsNone(result)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_empty_list_response_returns_none(self):
        server, thread, calls, url = _rerank_server(lambda body: (200, []))
        try:
            result = rerank(url, "m", "q", [(Chunk("d", 0, "s", "a"), 1.0)])
            self.assertIsNone(result)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_out_of_range_index_returns_none(self):
        server, thread, calls, url = _rerank_server(
            lambda body: (200, [{"index": 99, "score": 0.5}]))
        try:
            result = rerank(url, "m", "q", [(Chunk("d", 0, "s", "a"), 1.0)])
            self.assertIsNone(result)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_non_int_index_returns_none(self):
        server, thread, calls, url = _rerank_server(
            lambda body: (200, [{"index": "0", "score": 0.5}]))
        try:
            result = rerank(url, "m", "q", [(Chunk("d", 0, "s", "a"), 1.0)])
            self.assertIsNone(result)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_500_response_returns_none(self):
        server, thread, calls, url = _rerank_server(lambda body: (500, {"error": "boom"}))
        try:
            result = rerank(url, "m", "q", [(Chunk("d", 0, "s", "a"), 1.0)])
            self.assertIsNone(result)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)


class TestSearchDefaultOffIsNoOp(unittest.TestCase):
    def test_no_config_never_imports_or_calls_rerank(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_store(d, [("alpha", [1.0, 0.0]), ("beta", [0.0, 1.0])])
            with patch("aictl.core.rag.embed_text", return_value=[[1.0, 0.0]]), \
                 patch("aictl.core.rerank.rerank") as mock_rerank:
                result = search("q", store, k=2, config=None)
                mock_rerank.assert_not_called()
            self.assertEqual(len(result), 2)

    def test_default_config_rerank_endpoint_empty_never_calls_rerank(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_store(d, [("alpha", [1.0, 0.0]), ("beta", [0.0, 1.0])])
            with patch("aictl.core.rag.embed_text", return_value=[[1.0, 0.0]]), \
                 patch("aictl.core.rerank.rerank") as mock_rerank:
                search("q", store, k=2, config=Config())
                mock_rerank.assert_not_called()

    def test_config_default_rerank_fields(self):
        c = Config()
        self.assertEqual(c.rerank_endpoint, "")
        self.assertEqual(c.rerank_model, "")


class TestSearchWithRerankConfigured(unittest.TestCase):
    def test_real_server_changes_result_order(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_store(d, [
                ("first", [1.0, 0.0, 0.0]),
                ("second", [0.9, 0.1, 0.0]),
                ("third", [0.0, 1.0, 0.0]),
            ])

            def handler(body):
                n = len(body["texts"])
                return 200, [{"index": n - 1 - i, "score": 1.0 - i * 0.1} for i in range(n)]

            server, thread, calls, url = _rerank_server(handler)
            try:
                with patch("aictl.core.rag.embed_text", return_value=[[1.0, 0.0, 0.0]]):
                    cfg = Config(rerank_endpoint=url, rerank_model="bge-reranker-v2-m3")
                    result = search("first", store, k=3, config=cfg)
                self.assertEqual([c.text for c, _ in result], ["third", "second", "first"])
                self.assertEqual(calls["n"], 1)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_unreachable_endpoint_falls_back_to_rrf_order(self):
        with tempfile.TemporaryDirectory() as d:
            store = _make_store(d, [
                ("first", [1.0, 0.0, 0.0]),
                ("second", [0.9, 0.1, 0.0]),
            ])
            with patch("aictl.core.rag.embed_text", return_value=[[1.0, 0.0, 0.0]]):
                cfg = Config(rerank_endpoint="http://127.0.0.1:1", rerank_model="m")
                rrf_only = search("first", store, k=2, config=None)
                with_broken_rerank = search("first", store, k=2, config=cfg)
            self.assertEqual([c.text for c, _ in rrf_only], [c.text for c, _ in with_broken_rerank])

    def test_pool_size_widened_before_truncation(self):
        # k=1 but the reranker should see a wider pool (max(k*4, RERANK_POOL_MIN))
        # so it can promote a candidate RRF ranked outside the naive top-1.
        with tempfile.TemporaryDirectory() as d:
            texts_and_vecs = [(f"doc{i}", [1.0 - i * 0.01, i * 0.01, 0.0]) for i in range(15)]
            store = _make_store(d, texts_and_vecs)

            def handler(body):
                n = len(body["texts"])
                self.assertGreaterEqual(n, 15)  # whole pool, well beyond k=1
                # Promote the LAST candidate to rank 0.
                order = [n - 1] + list(range(n - 1))
                return 200, [{"index": idx, "score": 1.0 - i * 0.01} for i, idx in enumerate(order)]

            server, thread, calls, url = _rerank_server(handler)
            try:
                with patch("aictl.core.rag.embed_text", return_value=[[1.0, 0.0, 0.0]]):
                    cfg = Config(rerank_endpoint=url, rerank_model="m")
                    result = search("doc0", store, k=1, config=cfg)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0][0].text, "doc14")
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=5)


class TestConfigValidation(unittest.TestCase):
    def test_bad_scheme_rejected(self):
        from aictl.cmd.config import _validate_config
        c = Config()
        c.rerank_endpoint = "ftp://example.com"
        problems = _validate_config(c)
        self.assertTrue(any("rerank_endpoint" in p for p in problems))

    def test_http_and_https_accepted(self):
        from aictl.cmd.config import _validate_config
        for scheme in ("http://x:8080", "https://x:8080"):
            c = Config()
            c.rerank_endpoint = scheme
            problems = _validate_config(c)
            self.assertEqual([p for p in problems if "rerank_endpoint" in p], [])

    def test_empty_endpoint_accepted(self):
        from aictl.cmd.config import _validate_config
        problems = _validate_config(Config())
        self.assertEqual([p for p in problems if "rerank_endpoint" in p], [])

    def test_round_trip_through_save_and_load(self):
        from aictl.core.config import save_config, load_config
        with tempfile.TemporaryDirectory() as d:
            c = Config()
            c.rerank_endpoint = "http://localhost:8080"
            c.rerank_model = "bge-reranker-v2-m3"
            save_config(c, Path(d))
            loaded = load_config(Path(d))
            self.assertEqual(loaded.rerank_endpoint, "http://localhost:8080")
            self.assertEqual(loaded.rerank_model, "bge-reranker-v2-m3")


class TestCliWiring(unittest.TestCase):
    def test_search_and_ask_have_rerank_flag(self):
        from aictl.cmd.rag import register
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)

        ns = parser.parse_args(["rag", "search", "query text", "--rerank"])
        self.assertTrue(ns.rerank)
        ns2 = parser.parse_args(["rag", "ask", "a question", "--rerank"])
        self.assertTrue(ns2.rerank)

    def test_index_status_reset_have_no_rerank_flag(self):
        from aictl.cmd.rag import register
        import argparse
        import io
        from contextlib import redirect_stderr

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            parser.parse_args(["rag", "status", "--rerank"])

    def test_load_rerank_config_returns_none_without_flag(self):
        from aictl.cmd.rag import _load_rerank_config
        import argparse
        ns = argparse.Namespace(rerank=False)
        self.assertIsNone(_load_rerank_config(ns))

    def test_load_rerank_config_missing_attr_returns_none(self):
        # Older/other call sites building a bare Namespace without `rerank`
        # at all must not crash.
        from aictl.cmd.rag import _load_rerank_config
        import argparse
        ns = argparse.Namespace()
        self.assertIsNone(_load_rerank_config(ns))

    def test_load_rerank_config_warns_when_unconfigured(self):
        from aictl.cmd.rag import _load_rerank_config
        import argparse
        import io
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as d:
            with patch.dict("os.environ", {"AIOS_STATE_DIR": d}):
                ns = argparse.Namespace(rerank=True)
                buf = io.StringIO()
                with redirect_stderr(buf):
                    cfg = _load_rerank_config(ns)
                self.assertIsNotNone(cfg)
                self.assertIn("rerank_endpoint", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
