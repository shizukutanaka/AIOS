"""Pass 142: proxy _read_body must treat negative Content-Length as no body.

The completions proxy caps request bodies at 100 MB "to prevent memory
exhaustion" via `length = min(length, _MAX_BODY_BYTES)`. But the empty-body guard
was `if length == 0`, which a NEGATIVE Content-Length slips past:

    Content-Length: -5
      -> length = -5  (a valid int, not caught by the except)
      -> min(-5, 100MB) == -5
      -> rfile.read(-5)  reads until EOF  ->  cap BYPASSED

so a malicious client could stream an unbounded body and defeat the very DoS
guard the cap exists to enforce. The sibling daemon (aiosd._read_body) already
uses the correct `<= 0` test.

Fix: treat any non-positive length as "no body" (`if length <= 0: return {}`).
This regression drives the real ProxyHandler._read_body with a fake rfile to
prove a negative Content-Length never triggers an unbounded read.
"""

from __future__ import annotations

import io
import unittest


class _FakeHeaders:
    def __init__(self, content_length):
        self._cl = content_length

    def get(self, key, default=None):
        if key == "Content-Length":
            return self._cl
        return default


class _StubHandler:
    """Bind the real _read_body to a stub with controllable headers/rfile."""

    def __init__(self, content_length, body: bytes):
        self.headers = _FakeHeaders(content_length)
        self.rfile = io.BytesIO(body)

    # Reuse the actual implementation under test.
    from aictl.daemon.proxy import ProxyHandler as _H
    _read_body = _H._read_body


class TestProxyReadBodyNegativeContentLength(unittest.TestCase):
    def test_negative_content_length_returns_empty(self):
        huge = b"x" * 1_000_000
        h = _StubHandler("-5", huge)
        self.assertEqual(h._read_body(), {})
        # The unbounded read must NOT have consumed the stream.
        self.assertEqual(h.rfile.tell(), 0)

    def test_zero_content_length_returns_empty(self):
        h = _StubHandler("0", b"{}")
        self.assertEqual(h._read_body(), {})

    def test_malformed_content_length_returns_empty(self):
        h = _StubHandler("abc", b'{"a": 1}')
        self.assertEqual(h._read_body(), {})

    def test_valid_body_still_parsed(self):
        body = b'{"model": "llama", "stream": false}'
        h = _StubHandler(str(len(body)), body)
        self.assertEqual(h._read_body(), {"model": "llama", "stream": False})

    def test_huge_declared_length_capped(self):
        # A declared length far above the cap reads at most the cap, not more.
        from aictl.daemon.proxy import _MAX_BODY_BYTES
        body = b'{"ok": true}'
        h = _StubHandler(str(_MAX_BODY_BYTES * 10), body)
        # Should still parse the small actual body without error.
        self.assertEqual(h._read_body(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
