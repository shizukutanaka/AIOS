"""Pass 14 regression tests for correctness bugs identified by deep audit."""

import io
import json
import unittest
from unittest import mock


class TestAiosdMalformedJson(unittest.TestCase):
    """aiosd.py: malformed JSON in a POST body must return 400, not crash the handler."""

    def _make_handler(self, body_bytes, content_length=None):
        """Construct an AIOSHandler without running BaseHTTPRequestHandler.__init__."""
        from aictl.daemon.aiosd import AIOSHandler
        h = AIOSHandler.__new__(AIOSHandler)
        if content_length is None:
            content_length = str(len(body_bytes))
        h.headers = {"Content-Length": content_length}
        h.rfile = io.BytesIO(body_bytes)
        # Capture _json_response calls
        h._responses = []

        def fake_json_response(payload, status=200):
            h._responses.append((payload, status))

        h._json_response = fake_json_response
        return h

    def test_read_body_handles_bad_content_length(self):
        h = self._make_handler(b"", content_length="not-a-number")
        # Must not raise — returns empty dict
        result = h._read_body()
        self.assertEqual(result, {})

    def test_read_body_empty_returns_dict(self):
        h = self._make_handler(b"", content_length="0")
        self.assertEqual(h._read_body(), {})

    def test_read_body_valid_json(self):
        h = self._make_handler(b'{"name": "test"}')
        self.assertEqual(h._read_body(), {"name": "test"})

    def test_read_body_malformed_json_raises_decode_error(self):
        h = self._make_handler(b'{bad json}')
        # _read_body itself raises JSONDecodeError; do_POST catches it
        with self.assertRaises(json.JSONDecodeError):
            h._read_body()

    def test_do_post_malformed_json_returns_400(self):
        from aictl.daemon.aiosd import AIOSHandler
        h = AIOSHandler.__new__(AIOSHandler)
        h.path = "/v1/stacks/down"
        h.headers = {"Content-Length": "10"}
        h.rfile = io.BytesIO(b'{bad json}')
        responses = []
        h._json_response = lambda payload, status=200: responses.append((payload, status))
        # Should not raise; should produce a 400
        h.do_POST()
        self.assertTrue(responses, "do_POST must produce a response")
        payload, status = responses[-1]
        self.assertEqual(status, 400, "malformed JSON body must yield HTTP 400")


class TestKserveLabelConsistency(unittest.TestCase):
    """kserve.py: Deployment metadata label must match the selector/pod label format."""

    def test_deployment_label_matches_selector(self):
        from aictl.stack.manifest import ServiceDef
        from aictl.stack import kserve
        import inspect

        # Find the function that generates the Deployment + Service resources
        src = inspect.getsource(kserve)
        # The buggy pattern set the Deployment metadata label to bare svc.name
        # while the selector used f"{stack_name}-{svc.name}".
        self.assertNotIn(
            '"labels": {"aios.stack": stack_name, "aios.service": svc.name}',
            src,
            "kserve.py Deployment metadata label must use the {stack}-{svc} format "
            "to stay consistent with its selector and pod labels",
        )
        self.assertIn(
            'f"{stack_name}-{svc.name}"',
            src,
        )


if __name__ == "__main__":
    unittest.main()
