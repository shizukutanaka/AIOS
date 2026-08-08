"""Pass 197: self-audit of items T-Y — two honesty/consistency defects.

INSTRUCTIONS_OPUS.md calls code added in recent passes the highest-yield audit
surface. Re-reading items T-Y with fresh eyes turned up two defects that no
test covered because both live in paths the happy-path tests never take.

1. The KV offload vendor gate fell OPEN on an unnamed vendor. `if vendor and
   vendor.lower() not in SUPPORTED` meant a *recognized-but-unsupported*
   vendor ("huawei") correctly declined, while an *unidentified* one ("")
   skipped the check entirely and got a recommendation. The looser case was
   the one falling open — and emitting the flag for hardware the connector may
   not support produces a config the engine rejects at startup.

2. `engines conform` reported a false negative on chat. When /v1/models does
   not answer and no --model is given, the probe invents "test-model"; engines
   that validate the name (vLLM) reject it, and the report said "chat
   completions: HTTP 404" — indistinguishable from a genuinely broken
   endpoint. In a module whose entire purpose is honest reporting, silently
   conflating "your engine is broken" with "I guessed the model name wrong" is
   the worst kind of defect it could have.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from aictl.runtime.conformance import check_conformance
from aictl.runtime.kv_offload import SUPPORTED_VENDORS, advise_kv_offload

AMPLE_RAM = 128 * 1024
TIGHT_KV = 11_200


class TestVendorGateFailsClosed(unittest.TestCase):
    def test_unnamed_vendor_declines_like_an_unknown_one(self):
        advice = advise_kv_offload(host_ram_mb=AMPLE_RAM, gpu_kv_mb=TIGHT_KV,
                                   vendor="")
        self.assertFalse(advice.recommended)
        self.assertIn("unknown", advice.reason)

    def test_named_unsupported_vendor_still_declines(self):
        for vendor in ("huawei", "qualcomm", "apple", "cpu"):
            advice = advise_kv_offload(host_ram_mb=AMPLE_RAM, gpu_kv_mb=TIGHT_KV,
                                       vendor=vendor)
            self.assertFalse(advice.recommended, vendor)

    def test_supported_vendors_are_unaffected(self):
        for vendor in SUPPORTED_VENDORS:
            advice = advise_kv_offload(host_ram_mb=AMPLE_RAM, gpu_kv_mb=TIGHT_KV,
                                       vendor=vendor)
            self.assertTrue(advice.recommended, vendor)

    def test_every_broker_vendor_string_is_classified(self):
        # The broker emits these; each must produce a decision, never a crash.
        for vendor in ("nvidia", "amd", "intel", "apple", "huawei", "qualcomm"):
            advice = advise_kv_offload(host_ram_mb=AMPLE_RAM, gpu_kv_mb=TIGHT_KV,
                                       vendor=vendor)
            self.assertIsInstance(advice.recommended, bool)
            self.assertTrue(advice.reason or advice.flag, vendor)


def _picky_engine(*, serve_models: bool, valid_model: str = "real-model"):
    """Engine that validates model names, like vLLM, and optionally hides
    /v1/models — the combination that produced the false negative."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, payload=None):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if payload is not None:
                self.wfile.write(json.dumps(payload).encode())

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/health":
                self._send(200, {"status": "ok"})
            elif path == "/v1/models" and serve_models:
                self._send(200, {"data": [{"id": valid_model}]})
            else:
                self._send(503)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if body.get("model") == valid_model:
                self._send(200, {"choices": [{"message": {"content": "hi"}}]})
            else:
                self._send(404, {"error": "model not found"})

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{port}"


class TestConformanceDoesNotCryWolf(unittest.TestCase):
    def _probe(self, **kwargs):
        server, thread, url = _picky_engine(serve_models=kwargs.pop("serve_models"))
        try:
            report = check_conformance(url, timeout=3, **kwargs)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        return {p.name: p for p in report.probes}

    def test_guessed_model_name_failure_says_so(self):
        chat = self._probe(serve_models=False)["chat completions"]
        self.assertFalse(chat.ok)
        self.assertIn("guessed model name", chat.detail)
        self.assertIn("--model", chat.detail)

    def test_supplying_the_model_resolves_it(self):
        chat = self._probe(serve_models=False, model="real-model")["chat completions"]
        self.assertTrue(chat.ok)

    def test_discovered_model_is_used_without_a_caveat(self):
        # /v1/models answered, so the name is known — a failure here would be
        # a real failure and must not be softened with the caveat.
        chat = self._probe(serve_models=True)["chat completions"]
        self.assertTrue(chat.ok)
        self.assertNotIn("guessed", chat.detail)

    def test_caveat_absent_when_the_user_named_the_model(self):
        # An explicitly supplied name that fails IS a real signal; the caveat
        # would wrongly excuse it.
        server, thread, url = _picky_engine(serve_models=False)
        try:
            report = check_conformance(url, timeout=3, model="wrong-name")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        chat = {p.name: p for p in report.probes}["chat completions"]
        self.assertFalse(chat.ok)
        self.assertNotIn("guessed model name", chat.detail)


if __name__ == "__main__":
    unittest.main()
