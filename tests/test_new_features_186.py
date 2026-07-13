"""Pass 186 (IMPROVEMENTS.md item A-2, remaining half): embedding-provider
capability detection.

Closes the last open piece of item A-2: "formalize an embedding-provider
hook ... with capability detection, so the hash path is truly last-resort."
Before this pass, sdk._embed unconditionally guessed "nomic-embed-text" on
every call regardless of whether that (or any) embedding model was actually
pulled -- a wrong guess wastes a network round-trip on a request that's
virtually guaranteed to fail before falling back to the hash embedding
anyway.

_detect_embedding_model(endpoint) probes the engine's /v1/models (the
OpenAI-compatible listing every adapter in this project already speaks --
vLLM, SGLang, Ollama, LM Studio, TensorRT-LLM) and picks the best available
embedding-capable model from a 2026-consensus priority list (Pass 181
research: nomic-embed-text > bge-m3 > qwen3-embedding > bge-large >
bge-small > all-minilm). If nothing matches, _embed skips the doomed
/v1/embeddings call entirely and degrades straight to the hash fallback.

Detection is cached per-endpoint for the process lifetime (embed_text() is
on the hot path -- every semantic-cache lookup/store, every RAG query --
so re-probing on every call would add real latency for zero benefit once
an endpoint's roster is known; matches _AmbientContext's own
detect-once-per-process convention).
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch


def _models_server(model_ids):
    """One-shot local HTTP server that answers /v1/models with the given
    id list in OpenAI-compatible shape, and counts how many times it was hit."""
    calls = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls["n"] += 1
            body = json.dumps({
                "data": [{"id": mid, "object": "model"} for mid in model_ids],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]

    def serve():
        server.timeout = 0.2
        while not stop_event.is_set():
            server.handle_request()

    stop_event = threading.Event()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return server, thread, stop_event, calls, f"http://127.0.0.1:{port}"


class _CacheIsolated(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _reset_embedding_model_cache_for_testing
        _reset_embedding_model_cache_for_testing()

    def tearDown(self):
        from aictl.sdk import _reset_embedding_model_cache_for_testing
        _reset_embedding_model_cache_for_testing()


class TestDetectEmbeddingModel(_CacheIsolated):
    def test_picks_nomic_when_present(self):
        from aictl.sdk import _detect_embedding_model
        server, thread, stop_event, calls, url = _models_server(
            ["llama3.1:8b", "nomic-embed-text:latest", "qwen3:7b"])
        try:
            model = _detect_embedding_model(url)
            self.assertEqual(model, "nomic-embed-text:latest")
        finally:
            stop_event.set(); server.server_close(); thread.join(timeout=5)

    def test_priority_order_prefers_nomic_over_bge(self):
        from aictl.sdk import _detect_embedding_model
        server, thread, stop_event, calls, url = _models_server(
            ["bge-m3", "nomic-embed-text"])
        try:
            model = _detect_embedding_model(url)
            self.assertEqual(model, "nomic-embed-text")
        finally:
            stop_event.set(); server.server_close(); thread.join(timeout=5)

    def test_falls_back_through_priority_list(self):
        from aictl.sdk import _detect_embedding_model
        server, thread, stop_event, calls, url = _models_server(
            ["llama3.1:8b", "bge-small-en"])
        try:
            model = _detect_embedding_model(url)
            self.assertEqual(model, "bge-small-en")
        finally:
            stop_event.set(); server.server_close(); thread.join(timeout=5)

    def test_no_embedding_model_returns_none(self):
        from aictl.sdk import _detect_embedding_model
        server, thread, stop_event, calls, url = _models_server(
            ["llama3.1:8b", "qwen3:7b"])
        try:
            model = _detect_embedding_model(url)
            self.assertIsNone(model)
        finally:
            stop_event.set(); server.server_close(); thread.join(timeout=5)

    def test_unreachable_endpoint_returns_none_never_raises(self):
        from aictl.sdk import _detect_embedding_model
        model = _detect_embedding_model("http://127.0.0.1:1")
        self.assertIsNone(model)

    def test_malformed_response_returns_none(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"not json")

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            from aictl.sdk import _detect_embedding_model
            model = _detect_embedding_model(f"http://127.0.0.1:{port}")
            self.assertIsNone(model)
        finally:
            thread.join(timeout=5)
            server.server_close()


class TestDetectionCaching(_CacheIsolated):
    def test_detection_only_probes_once_per_endpoint(self):
        from aictl.sdk import _detect_embedding_model
        server, thread, stop_event, calls, url = _models_server(["nomic-embed-text"])
        try:
            for _ in range(5):
                _detect_embedding_model(url)
            self.assertEqual(calls["n"], 1, "5 calls to the same endpoint must probe once")
        finally:
            stop_event.set(); server.server_close(); thread.join(timeout=5)

    def test_different_endpoints_probed_independently(self):
        from aictl.sdk import _detect_embedding_model
        s1, t1, e1, c1, url1 = _models_server(["nomic-embed-text"])
        s2, t2, e2, c2, url2 = _models_server(["bge-m3"])
        try:
            m1 = _detect_embedding_model(url1)
            m2 = _detect_embedding_model(url2)
            self.assertEqual(m1, "nomic-embed-text")
            self.assertEqual(m2, "bge-m3")
        finally:
            e1.set(); s1.server_close(); t1.join(timeout=5)
            e2.set(); s2.server_close(); t2.join(timeout=5)

    def test_negative_result_is_also_cached(self):
        # A probe failure (or genuinely no embedding model) must not be
        # re-attempted every single embed() call on the hot path.
        from aictl.sdk import _detect_embedding_model
        with patch("urllib.request.urlopen", side_effect=OSError("down")) as mock_open:
            _detect_embedding_model("http://127.0.0.1:9")
            _detect_embedding_model("http://127.0.0.1:9")
            _detect_embedding_model("http://127.0.0.1:9")
        self.assertEqual(mock_open.call_count, 1)

    def test_reset_clears_cache(self):
        from aictl.sdk import _detect_embedding_model, _reset_embedding_model_cache_for_testing
        server, thread, stop_event, calls, url = _models_server(["nomic-embed-text"])
        try:
            _detect_embedding_model(url)
            _reset_embedding_model_cache_for_testing()
            _detect_embedding_model(url)
            self.assertEqual(calls["n"], 2, "reset must force a fresh probe")
        finally:
            stop_event.set(); server.server_close(); thread.join(timeout=5)


class TestEmbedUsesDetectedModel(_CacheIsolated):
    def test_embed_skips_network_call_when_no_model_detected(self):
        # No embedding model available -> _embed must not even attempt
        # /v1/embeddings, going straight to the hash fallback.
        from aictl import sdk as sdk_mod
        from aictl.core.rag import FALLBACK_DIM
        server, thread, stop_event, calls, url = _models_server(["llama3.1:8b"])
        try:
            vectors = sdk_mod._embed(url, ["hello"])
            self.assertEqual(len(vectors[0]), FALLBACK_DIM)
            # Exactly one GET (the /v1/models probe) -- no /v1/embeddings POST.
            self.assertEqual(calls["n"], 1)
        finally:
            stop_event.set(); server.server_close(); thread.join(timeout=5)

    def test_embed_uses_the_detected_model_name_in_the_request(self):
        from aictl import sdk as sdk_mod
        captured_model = {}

        def fake_urlopen(req, timeout=0):
            body = json.loads(req.data)
            captured_model["model"] = body["model"]

            class _R:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return json.dumps({"data": [{"embedding": [0.2] * 768}]}).encode()
            return _R()

        with patch.object(sdk_mod, "_detect_embedding_model", return_value="bge-m3-custom"), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            vectors = sdk_mod._embed("http://fake-endpoint", ["text"])
        self.assertEqual(captured_model["model"], "bge-m3-custom")
        self.assertEqual(len(vectors[0]), 768)


if __name__ == "__main__":
    unittest.main()
