"""Pass 144: mock_engine request-body reader must survive malformed headers.

The mock engine (which backs `aictl demo` and much of the test suite) read POST
bodies with a bare:

    content_length = int(self.headers.get("Content-Length", 0))
    body = json.loads(self.rfile.read(content_length)) if content_length else {}

duplicated across /v1/chat/completions and /api/generate. Two latent faults, the
same family fixed in proxy._read_body (Pass 142):

  - a non-numeric Content-Length raised ValueError from int(), crashing the
    handler thread;
  - a NEGATIVE Content-Length is truthy, so `rfile.read(-5)` read until EOF,
    defeating any body-size bound; and malformed JSON raised JSONDecodeError.

Fix: a single hardened `_read_json_body()` (mirroring aiosd/proxy) that wraps the
int parse, treats any non-positive length as "no body", caps the read at
_MAX_BODY_BYTES, and returns {} on malformed JSON. Both endpoints now use it.
"""

from __future__ import annotations

import io
import unittest


class _Headers(dict):
    """Minimal stand-in for BaseHTTPRequestHandler.headers (supports .get)."""


def _handler(content_length, body: bytes):
    from aictl.daemon.mock_engine import MockEngineHandler
    h = MockEngineHandler.__new__(MockEngineHandler)
    h.headers = _Headers()
    h.headers["Content-Length"] = content_length
    h.rfile = io.BytesIO(body)
    return h


class TestMockEngineReadJsonBody(unittest.TestCase):
    def test_valid_body_parsed(self):
        h = _handler("13", b'{"model":"x"}')
        self.assertEqual(h._read_json_body(), {"model": "x"})

    def test_negative_content_length_no_eof_read(self):
        h = _handler("-5", b"x" * 100_000)
        self.assertEqual(h._read_json_body(), {})
        self.assertEqual(h.rfile.tell(), 0)   # never consumed the stream

    def test_malformed_content_length_no_crash(self):
        self.assertEqual(_handler("abc", b"{}")._read_json_body(), {})

    def test_zero_content_length(self):
        self.assertEqual(_handler("0", b"")._read_json_body(), {})

    def test_malformed_json_returns_empty(self):
        self.assertEqual(_handler("3", b"{xx")._read_json_body(), {})

    def test_oversized_declared_length_capped(self):
        from aictl.daemon.mock_engine import MockEngineHandler
        body = b'{"ok": true}'
        h = _handler(str(MockEngineHandler._MAX_BODY_BYTES * 5), body)
        self.assertEqual(h._read_json_body(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
