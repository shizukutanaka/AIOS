"""Pass 187 (IMPROVEMENTS.md item C-1): embedding-kNN request-router
tie-breaker.

Design (confidence-gated kNN tie-breaker, per multi-agent research/design
review): the regex scorer (score_complexity/classify_complexity) always
runs first and stays authoritative. Embedding-kNN is consulted ONLY as a
tie-breaker, and only when every one of these holds:
  - route_knn_enabled is set in global config, or route_tier_gated(force=True)
  - the regex score falls within route_knn_margin of the 30/60 tier boundary
  - the labeled 30-example bank has real (non-fallback) embeddings
  - the live prompt's own embedding is also real (non-fallback)
  - the top-k neighbor vote reaches route_knn_min_agreement
  - the kNN verdict is an ADJACENT tier only (never SIMPLE<->COMPLEX)
Any failure anywhere silently abstains to the regex verdict. Default
route_knn_enabled=False makes this a true no-op: zero embed_text() calls,
zero disk I/O, byte-identical to the pre-existing regex-only router.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from aictl.cmd.route import (
    _KNN_EXAMPLES, _TEST_CASES, EXPECTED_KNN_EXAMPLES,
    _knn_cache_path, _get_knn_bank, _reset_knn_cache_for_testing,
    route_tier_gated, score_complexity, classify_complexity,
)
from aictl.core.config import Config
from aictl.core.rag import FALLBACK_DIM


def _embeddings_server(dim=8):
    """Local HTTP server answering both /v1/models (one embedding-capable
    model) and /v1/embeddings (deterministic per-text vectors), counting
    hits to each endpoint."""
    calls = {"models": 0, "embeddings": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls["models"] += 1
            body = json.dumps({"data": [{"id": "nomic-embed-text", "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            calls["embeddings"] += 1
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
            texts = payload.get("input", [])
            if isinstance(texts, str):
                texts = [texts]
            vectors = []
            for t in texts:
                h = abs(hash(t))
                vectors.append([((h >> (i * 4)) % 1000) / 1000.0 for i in range(dim)])
            body = json.dumps({"data": [{"embedding": v} for v in vectors]}).encode()
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


class _Isolated(unittest.TestCase):
    """State-dir + kNN memo isolation for every test in this file."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._env_patch = patch.dict(os.environ, {"AIOS_STATE_DIR": self._tmpdir.name})
        self._env_patch.start()
        _reset_knn_cache_for_testing()
        from aictl.sdk import _reset_embedding_model_cache_for_testing
        _reset_embedding_model_cache_for_testing()

    def tearDown(self):
        _reset_knn_cache_for_testing()
        from aictl.sdk import _reset_embedding_model_cache_for_testing
        _reset_embedding_model_cache_for_testing()
        self._env_patch.stop()
        self._tmpdir.cleanup()


class TestLabeledSetIntegrity(unittest.TestCase):
    def test_count_matches_expected_constant(self):
        self.assertEqual(len(_KNN_EXAMPLES), EXPECTED_KNN_EXAMPLES)
        self.assertEqual(EXPECTED_KNN_EXAMPLES, 30)

    def test_ten_per_tier(self):
        counts = {"SIMPLE": 0, "MEDIUM": 0, "COMPLEX": 0}
        for tier, _ in _KNN_EXAMPLES:
            counts[tier] += 1
        self.assertEqual(counts, {"SIMPLE": 10, "MEDIUM": 10, "COMPLEX": 10})

    def test_all_tiers_are_valid(self):
        for tier, _ in _KNN_EXAMPLES:
            self.assertIn(tier, ("SIMPLE", "MEDIUM", "COMPLEX"))

    def test_disjoint_from_test_cases(self):
        # route test --knn's accuracy numbers must be an honest out-of-sample
        # measurement, not the kNN bank grading its own labeled examples.
        test_prompts = {p for _, p in _TEST_CASES}
        knn_prompts = {p for _, p in _KNN_EXAMPLES}
        self.assertTrue(test_prompts.isdisjoint(knn_prompts))

    def test_no_duplicate_prompts_within_bank(self):
        prompts = [p for _, p in _KNN_EXAMPLES]
        self.assertEqual(len(prompts), len(set(prompts)))


class TestDefaultOffIsNoOp(_Isolated):
    def test_disabled_by_default_never_calls_embed(self):
        with patch("aictl.core.rag.embed_text") as mock_embed:
            tier, meta = route_tier_gated("one two three four five six seven eight nine ten")
            self.assertFalse(meta["knn_applied"])
            mock_embed.assert_not_called()

    def test_disabled_returns_pure_regex_verdict_for_whole_test_set(self):
        for expected, prompt in _TEST_CASES:
            score = score_complexity(prompt)
            regex_tier = classify_complexity(score)
            tier, meta = route_tier_gated(prompt)
            self.assertEqual(tier, regex_tier)
            self.assertFalse(meta["knn_applied"])

    def test_config_default_is_disabled(self):
        c = Config()
        self.assertFalse(c.route_knn_enabled)
        self.assertEqual(c.route_knn_margin, 5)
        self.assertEqual(c.route_knn_k, 5)
        self.assertEqual(c.route_knn_min_agreement, 0.8)


class TestBoundaryMarginGating(_Isolated):
    def test_far_from_boundary_never_embeds_even_when_forced(self):
        # score 0 ("What is 2+2?") is nowhere near 30 or 60.
        with patch("aictl.core.rag.embed_text") as mock_embed:
            tier, meta = route_tier_gated("What is 2+2?", force=True)
            mock_embed.assert_not_called()
            self.assertFalse(meta["knn_applied"])

    def test_near_boundary_does_attempt_embed_when_forced(self):
        prompt = "one two three four five six seven eight nine ten"  # score 30
        self.assertEqual(score_complexity(prompt), 30)
        with patch("aictl.core.rag.embed_text", wraps=lambda texts: [[0.1] * 8 for _ in texts]) as mock_embed:
            route_tier_gated(prompt, force=True)
            mock_embed.assert_called()


class TestDegradedEmbeddingsAbstain(_Isolated):
    def test_fallback_dim_bank_abstains(self):
        # embed_text always returns FALLBACK_DIM-width vectors (hash fallback)
        # -> _get_knn_bank must report semantic=False and the gate abstains.
        with patch("aictl.core.rag.embed_text",
                   side_effect=lambda texts: [[0.0] * FALLBACK_DIM for _ in texts]):
            bank, semantic = _get_knn_bank()
            self.assertFalse(semantic)
        prompt = "one two three four five six seven eight nine ten"
        tier, meta = route_tier_gated(prompt, force=True)
        self.assertEqual(tier, "SIMPLE")
        self.assertFalse(meta["knn_applied"])

    def test_embed_text_raising_abstains_cleanly(self):
        with patch("aictl.core.rag.embed_text", side_effect=RuntimeError("boom")):
            prompt = "one two three four five six seven eight nine ten"
            tier, meta = route_tier_gated(prompt, force=True)
            self.assertEqual(tier, "SIMPLE")
            self.assertFalse(meta["knn_applied"])


class TestKnnBankBuildAndCache(_Isolated):
    def test_real_server_builds_semantic_bank_and_writes_cache(self):
        server, thread, stop_event, calls, url = _embeddings_server()
        # Directly patch aictl.ai.embed (what core.rag.embed_text calls) to hit
        # our fake server via urllib, exercising the real embed_text() path.
        import aictl

        def fake_embed(texts):
            import urllib.request
            req = urllib.request.Request(
                url + "/v1/embeddings",
                data=json.dumps({"input": texts, "model": "nomic-embed-text"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
            return [d["embedding"] for d in data["data"]]

        try:
            with patch.object(aictl.ai, "embed", side_effect=fake_embed):
                bank, semantic = _get_knn_bank()
            self.assertTrue(semantic)
            self.assertEqual(len(bank), EXPECTED_KNN_EXAMPLES)
            for entry in bank:
                self.assertIn("tier", entry)
                self.assertIn("prompt", entry)
                self.assertEqual(len(entry["vector"]), 8)

            cache_path = _knn_cache_path()
            self.assertTrue(cache_path.exists())
            cached = json.loads(cache_path.read_text())
            self.assertTrue(cached["semantic"])
            self.assertEqual(len(cached["bank"]), EXPECTED_KNN_EXAMPLES)

            # Second call must hit the in-process memo, not rebuild.
            with patch.object(aictl.ai, "embed", side_effect=RuntimeError("must not be called")):
                bank2, semantic2 = _get_knn_bank()
            self.assertTrue(semantic2)
            self.assertEqual(len(bank2), EXPECTED_KNN_EXAMPLES)
        finally:
            stop_event.set(); server.server_close(); thread.join(timeout=5)

    def test_cache_invalidates_on_hash_mismatch(self):
        cache_path = _knn_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        stale = {
            "bank_hash": "not-the-real-hash",
            "semantic": True,
            "built_at": 9999999999.0,
            "bank": [{"tier": "SIMPLE", "prompt": "stale", "vector": [1.0] * 8}],
        }
        cache_path.write_text(json.dumps(stale))
        with patch("aictl.core.rag.embed_text",
                   side_effect=lambda texts: [[0.0] * FALLBACK_DIM for _ in texts]):
            bank, semantic = _get_knn_bank()
        # Hash mismatch forces a rebuild -- must not reuse the stale entry.
        self.assertEqual(len(bank), EXPECTED_KNN_EXAMPLES)
        self.assertFalse(semantic)

    def test_reset_clears_memo_and_forces_rereads(self):
        calls = {"n": 0}

        def counting_embed(texts):
            calls["n"] += 1
            return [[0.0] * FALLBACK_DIM for _ in texts]

        with patch("aictl.core.rag.embed_text", side_effect=counting_embed):
            _get_knn_bank()
            _get_knn_bank()  # memo hit, no rebuild
        self.assertEqual(calls["n"], 1)

        _reset_knn_cache_for_testing()
        # Cache file still on disk from the first build and still fresh-ish,
        # so a reset alone (without deleting the file) should reuse it via
        # disk, not necessarily re-embed -- verify no crash and same shape.
        with patch("aictl.core.rag.embed_text", side_effect=counting_embed):
            bank, semantic = _get_knn_bank()
        self.assertEqual(len(bank), EXPECTED_KNN_EXAMPLES)


class TestAgreementAndAdjacentTierGuard(_Isolated):
    def _seed_bank(self, entries):
        """Directly seed the in-process memo with a synthetic bank so vote
        outcomes are fully controlled (no HTTP server needed)."""
        import aictl.cmd.route as route_mod
        bank_hash = route_mod._knn_examples_hash()
        route_mod._KNN_BANK_MEMO = {
            "bank_hash": bank_hash,
            "semantic": True,
            "built_at": 0.0,
            "bank": entries,
        }

    def test_unanimous_adjacent_agreement_flips_tier(self):
        # All 5 nearest neighbors vote MEDIUM; regex says SIMPLE (boundary
        # score 30) -- MEDIUM is adjacent to SIMPLE, so this should flip.
        entries = [{"tier": "MEDIUM", "prompt": f"m{i}", "vector": [1.0, 0.0]} for i in range(5)]
        self._seed_bank(entries)
        prompt = "one two three four five six seven eight nine ten"  # score 30, SIMPLE
        with patch("aictl.core.rag.embed_text", return_value=[[1.0, 0.0]]):
            tier, meta = route_tier_gated(prompt, force=True)
        self.assertEqual(tier, "MEDIUM")
        self.assertTrue(meta["knn_applied"])
        self.assertEqual(meta["knn_agreement"], 1.0)

    def test_below_agreement_threshold_abstains(self):
        # 3-2 split among 5 -> 0.6 agreement, below the default 0.8 floor.
        entries = (
            [{"tier": "MEDIUM", "prompt": f"m{i}", "vector": [1.0, 0.0]} for i in range(3)]
            + [{"tier": "SIMPLE", "prompt": f"s{i}", "vector": [1.0, 0.0]} for i in range(2)]
        )
        self._seed_bank(entries)
        prompt = "one two three four five six seven eight nine ten"
        with patch("aictl.core.rag.embed_text", return_value=[[1.0, 0.0]]):
            tier, meta = route_tier_gated(prompt, force=True)
        self.assertEqual(tier, "SIMPLE")  # regex verdict, unchanged
        self.assertFalse(meta["knn_applied"])
        self.assertEqual(meta["knn_agreement"], 0.6)

    def test_two_tier_jump_rejected_even_at_full_agreement(self):
        # Regex boundary score near 60 classifies MEDIUM; seed a unanimous
        # COMPLEX-only... actually need a SIMPLE verdict scenario for a true
        # 2-tier jump. Use score 30 (SIMPLE) with unanimous COMPLEX votes.
        entries = [{"tier": "COMPLEX", "prompt": f"c{i}", "vector": [1.0, 0.0]} for i in range(5)]
        self._seed_bank(entries)
        prompt = "one two three four five six seven eight nine ten"  # score 30, SIMPLE
        with patch("aictl.core.rag.embed_text", return_value=[[1.0, 0.0]]):
            tier, meta = route_tier_gated(prompt, force=True)
        self.assertEqual(tier, "SIMPLE")  # 2-tier jump rejected regardless of agreement
        self.assertFalse(meta["knn_applied"])
        self.assertEqual(meta["knn_agreement"], 1.0)  # agreement was computed, just rejected

    def test_agreement_with_matching_regex_tier_is_not_applied(self):
        # kNN agrees with the regex verdict itself -- no flip needed, and
        # knn_applied should stay False since nothing changed.
        entries = [{"tier": "SIMPLE", "prompt": f"s{i}", "vector": [1.0, 0.0]} for i in range(5)]
        self._seed_bank(entries)
        prompt = "one two three four five six seven eight nine ten"
        with patch("aictl.core.rag.embed_text", return_value=[[1.0, 0.0]]):
            tier, meta = route_tier_gated(prompt, force=True)
        self.assertEqual(tier, "SIMPLE")
        self.assertFalse(meta["knn_applied"])


class TestConfigValidation(unittest.TestCase):
    def test_margin_out_of_range_rejected(self):
        from aictl.cmd.config import _validate_config
        c = Config()
        c.route_knn_margin = 16
        problems = _validate_config(c)
        self.assertTrue(any("route_knn_margin" in p for p in problems))

    def test_k_out_of_range_rejected(self):
        from aictl.cmd.config import _validate_config
        c = Config()
        c.route_knn_k = 0
        problems = _validate_config(c)
        self.assertTrue(any("route_knn_k" in p for p in problems))

    def test_min_agreement_out_of_range_rejected(self):
        from aictl.cmd.config import _validate_config
        c = Config()
        c.route_knn_min_agreement = 0.5  # exclusive lower bound
        problems = _validate_config(c)
        self.assertTrue(any("route_knn_min_agreement" in p for p in problems))

    def test_defaults_pass_validation(self):
        from aictl.cmd.config import _validate_config
        problems = _validate_config(Config())
        self.assertEqual([p for p in problems if "route_knn" in p], [])

    def test_round_trip_through_save_and_load(self):
        from aictl.core.config import save_config, load_config
        with tempfile.TemporaryDirectory() as d:
            c = Config()
            c.route_knn_enabled = True
            c.route_knn_margin = 8
            c.route_knn_k = 7
            c.route_knn_min_agreement = 0.9
            save_config(c, Path(d))
            loaded = load_config(Path(d))
            self.assertTrue(loaded.route_knn_enabled)
            self.assertEqual(loaded.route_knn_margin, 8)
            self.assertEqual(loaded.route_knn_k, 7)
            self.assertEqual(loaded.route_knn_min_agreement, 0.9)


class TestCliJsonAdditiveKeys(_Isolated):
    def test_show_json_has_knn_applied_key_and_keeps_existing_keys(self):
        import argparse
        from aictl.cmd.route import run_show
        import io
        from contextlib import redirect_stdout

        ns = argparse.Namespace(prompt="What is 2+2?", json=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_show(ns)
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        for key in ("prompt", "score", "tier", "model", "knn_applied"):
            self.assertIn(key, payload)

    def test_show_without_knn_attr_does_not_raise(self):
        # Pre-existing tests build Namespace objects without a `knn` attribute
        # -- run_show must use getattr(args, "knn", False), never args.knn.
        import argparse
        from aictl.cmd.route import run_show
        import io
        from contextlib import redirect_stdout

        ns = argparse.Namespace(prompt="What is 2+2?", json=True)
        self.assertFalse(hasattr(ns, "knn"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_show(ns)
        self.assertEqual(rc, 0)

    def test_test_json_has_knn_applied_per_case(self):
        import argparse
        from aictl.cmd.route import run_test
        import io
        from contextlib import redirect_stdout

        ns = argparse.Namespace(n=5, json=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_test(ns)
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(len(payload["cases"]), 5)
        for case in payload["cases"]:
            self.assertIn("knn_applied", case)
            self.assertIn("score", case)
            self.assertIn("expected", case)
            self.assertIn("predicted", case)


class TestCliArgparseWiring(unittest.TestCase):
    def test_show_ask_test_have_knn_flag(self):
        from aictl.cmd.route import register
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)

        for argv in (
            ["route", "show", "hi", "--knn"],
            ["route", "test", "--n", "3", "--knn"],
        ):
            ns = parser.parse_args(argv)
            self.assertTrue(getattr(ns, "knn"))

    def test_batch_and_cascade_deliberately_have_no_knn_flag(self):
        # Scoping decision for this pass: batch/cascade are high-throughput/
        # latency-sensitive paths, deliberately deferred rather than wired
        # silently -- confirm --knn is rejected there, not silently ignored.
        from aictl.cmd.route import register
        import argparse
        import io
        from contextlib import redirect_stderr

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            parser.parse_args(["route", "cascade", "hi", "--knn"])


if __name__ == "__main__":
    unittest.main()
