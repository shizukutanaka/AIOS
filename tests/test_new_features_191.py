"""Pass 191 (First Principles gap): engine conformance checking.

First-principles analysis of the 80-command surface mapped every command onto
the 8-step chain that "run local AI inference well" reduces to. The finding:
aictl's entire value rests on an inference engine behaving as expected, yet
nothing verified that. `aictl selftest` never contacts an engine (zero
endpoint/urlopen references), and the 3433-test suite plus `aictl demo`
exercise only the bundled mock engine — proving internal consistency, not
that a user's real engine speaks what aictl needs. Users discovered
non-conformance mid-request, as silent quality loss (no /v1/embeddings →
RAG and the semantic cache fall back to the non-semantic hash embedding).

`aictl engines conform <url>` probes the six HTTP surfaces aictl depends on
and maps each to the features it powers, so a missing surface reads as
"rag/cache lose semantic search" rather than "404". Added as a subcommand of
the existing `engines` command rather than an 81st top-level command —
consistent with the same analysis, which flagged command sprawl as the
project's main excess (docs/REVIEW_v1.7.0.md).
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from aictl.runtime.conformance import (
    DEGRADED, OPTIONAL, REQUIRED, check_conformance,
)


def _engine(*, models=True, chat=True, stream=True, embeddings=True,
            metrics=True, health=True, malformed_models=False):
    """Local HTTP server emulating an OpenAI-compatible engine with a
    configurable subset of surfaces present."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, payload=None, raw=None):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if raw is not None:
                self.wfile.write(raw)
            elif payload is not None:
                self.wfile.write(json.dumps(payload).encode())

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/health":
                self._send(200 if health else 404, {"status": "ok"} if health else None)
            elif path == "/v1/models":
                if not models:
                    self._send(404)
                elif malformed_models:
                    self._send(200, raw=b"not json")
                else:
                    self._send(200, {"data": [{"id": "test-model", "object": "model"}]})
            elif path == "/metrics":
                self._send(200 if metrics else 404,
                           raw=b"# HELP x\n" if metrics else None)
            else:
                self._send(404)

        def do_POST(self):
            path = self.path.split("?")[0]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if path == "/v1/chat/completions":
                if body.get("stream"):
                    if not stream:
                        self._send(400)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
                    self.wfile.write(b"data: [DONE]\n\n")
                elif chat:
                    self._send(200, {"choices": [{"message": {"content": "pong"}}]})
                else:
                    self._send(404)
            elif path == "/v1/embeddings":
                self._send(200, {"data": [{"embedding": [0.1] * 768}]}) if embeddings \
                    else self._send(404)
            else:
                self._send(404)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{port}"


def _by_name(report):
    return {p.name: p for p in report.probes}


class TestFullyConformantEngine(unittest.TestCase):
    def test_all_probes_pass(self):
        server, thread, url = _engine()
        try:
            report = check_conformance(url, timeout=3)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertTrue(report.reachable)
        self.assertTrue(report.conformant, [p.name for p in report.probes if not p.ok])
        self.assertEqual(len(report.probes), 6)
        self.assertEqual(report.failed_required, [])
        self.assertEqual(report.failed_degraded, [])

    def test_embedding_dimension_reported(self):
        server, thread, url = _engine()
        try:
            report = check_conformance(url, timeout=3)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertIn("768", _by_name(report)["embeddings"].detail)


class TestMissingEmbeddings(unittest.TestCase):
    """The motivating case: engine works, but retrieval silently degrades."""

    def test_missing_embeddings_is_degraded_not_required(self):
        server, thread, url = _engine(embeddings=False)
        try:
            report = check_conformance(url, timeout=3)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        emb = _by_name(report)["embeddings"]
        self.assertFalse(emb.ok)
        self.assertEqual(emb.severity, DEGRADED)
        self.assertFalse(report.conformant)
        # Engine is still usable for inference — required probes all pass.
        self.assertEqual(report.failed_required, [])
        self.assertTrue(report.reachable)

    def test_impact_names_the_affected_features(self):
        server, thread, url = _engine(embeddings=False)
        try:
            report = check_conformance(url, timeout=3)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        emb = _by_name(report)["embeddings"]
        self.assertIn("hash embedding", emb.impact)
        joined = " ".join(emb.powers)
        for feature in ("rag", "semantic cache", "route --knn"):
            self.assertIn(feature, joined)


class TestDegradedAndBrokenEngines(unittest.TestCase):
    def test_missing_chat_is_required_failure(self):
        server, thread, url = _engine(chat=False)
        try:
            report = check_conformance(url, timeout=3)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        chat = _by_name(report)["chat completions"]
        self.assertFalse(chat.ok)
        self.assertEqual(chat.severity, REQUIRED)
        self.assertIn(chat, report.failed_required)

    def test_missing_metrics_is_optional_only(self):
        server, thread, url = _engine(metrics=False)
        try:
            report = check_conformance(url, timeout=3)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        metrics = _by_name(report)["metrics"]
        self.assertFalse(metrics.ok)
        self.assertEqual(metrics.severity, OPTIONAL)
        # Optional failures must not make the engine non-conformant.
        self.assertTrue(report.conformant)

    def test_missing_streaming_is_optional_only(self):
        server, thread, url = _engine(stream=False)
        try:
            report = check_conformance(url, timeout=3)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertFalse(_by_name(report)["streaming"].ok)
        self.assertTrue(report.conformant)

    def test_no_health_endpoint_still_reachable_via_models(self):
        # Many engines (vLLM) have no /health; /v1/models answering is enough.
        server, thread, url = _engine(health=False)
        try:
            report = check_conformance(url, timeout=3)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertTrue(report.reachable)
        self.assertIn("/v1/models answered", _by_name(report)["reachability"].detail)

    def test_malformed_models_json_is_a_failure_not_a_crash(self):
        server, thread, url = _engine(malformed_models=True)
        try:
            report = check_conformance(url, timeout=3)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        listing = _by_name(report)["model listing"]
        self.assertFalse(listing.ok)
        self.assertIn("malformed", listing.detail)


class TestUnreachableAndInvalid(unittest.TestCase):
    def test_unreachable_never_raises_and_keeps_report_shape(self):
        report = check_conformance("http://127.0.0.1:1", timeout=1)
        self.assertFalse(report.reachable)
        self.assertFalse(report.conformant)
        # Shape must stay stable for --json consumers even when nothing answered.
        self.assertEqual(len(report.probes), 6)
        self.assertEqual(
            [p.name for p in report.probes],
            ["model listing", "reachability", "chat completions", "streaming",
             "embeddings", "metrics"],
        )

    def test_non_http_scheme_rejected_without_request(self):
        report = check_conformance("file:///etc/passwd")
        self.assertFalse(report.reachable)
        self.assertIn("not an http(s) URL", report.probes[0].detail)

    def test_to_dict_is_json_serializable(self):
        report = check_conformance("http://127.0.0.1:1", timeout=1)
        payload = json.loads(json.dumps(report.to_dict()))
        self.assertEqual(
            sorted(payload.keys()), ["conformant", "endpoint", "probes", "reachable"])
        for probe in payload["probes"]:
            self.assertEqual(
                sorted(probe.keys()),
                ["detail", "impact", "name", "ok", "path", "powers", "severity"])


class TestCliWiring(unittest.TestCase):
    def test_conform_subcommand_registered(self):
        import argparse
        from aictl.cmd.engines import register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        ns = parser.parse_args(["engines", "conform", "http://x", "--strict", "--json"])
        self.assertEqual(ns.engines_cmd, "conform")
        self.assertEqual(ns.url, "http://x")
        self.assertTrue(ns.strict)
        self.assertTrue(ns.json)

    def test_url_is_optional(self):
        import argparse
        from aictl.cmd.engines import register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        ns = parser.parse_args(["engines", "conform"])
        self.assertEqual(ns.url, "")

    def test_strict_exits_nonzero_on_degraded_engine(self):
        import argparse
        import io
        from contextlib import redirect_stdout
        from aictl.cmd.engines import run_conform

        server, thread, url = _engine(embeddings=False)
        try:
            ns = argparse.Namespace(url=url, model="", strict=True, json=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_conform(ns)
            self.assertEqual(rc, 1)
            self.assertFalse(json.loads(buf.getvalue())[0]["conformant"])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_without_strict_exits_zero_even_when_degraded(self):
        import argparse
        import io
        from contextlib import redirect_stdout
        from aictl.cmd.engines import run_conform

        server, thread, url = _engine(embeddings=False)
        try:
            ns = argparse.Namespace(url=url, model="", strict=False, json=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_conform(ns)
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("embeddings", out)
            self.assertIn("hash embedding", out)  # the impact line, not just a code
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def test_missing_attrs_do_not_crash(self):
        # Namespaces built by other call sites may lack the new attrs entirely.
        import argparse
        import io
        from contextlib import redirect_stdout
        from aictl.cmd.engines import run_conform

        server, thread, url = _engine()
        try:
            ns = argparse.Namespace(url=url)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_conform(ns)
            self.assertEqual(rc, 0)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)


class TestAgainstRealMockEngine(unittest.TestCase):
    """Run against the project's own mock engine — a real HTTP server, not a
    hand-rolled double. It genuinely lacks /v1/embeddings, so this is a live
    demonstration of the degradation case the feature exists to surface."""

    def test_mock_engine_reports_missing_embeddings(self):
        from aictl.daemon.mock_engine import start_mock_engine

        server = start_mock_engine(port=0)
        port = server.server_address[1]
        try:
            report = check_conformance(f"http://127.0.0.1:{port}", timeout=3)
        finally:
            server.shutdown(); server.server_close()

        self.assertTrue(report.reachable)
        probes = _by_name(report)
        self.assertTrue(probes["chat completions"].ok)
        self.assertTrue(probes["model listing"].ok)
        self.assertFalse(probes["embeddings"].ok)     # mock has no /v1/embeddings
        self.assertEqual(report.failed_required, [])  # still usable for inference


if __name__ == "__main__":
    unittest.main()
