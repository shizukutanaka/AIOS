"""Tests for structured output / guided decoding support."""

import json
import sys
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.daemon.mock_engine import _generate_structured, start_mock_engine


class TestGenerateStructured(unittest.TestCase):
    def test_simple_object(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        result = json.loads(_generate_structured(schema))
        self.assertIn("name", result)
        self.assertIn("age", result)
        self.assertIsInstance(result["name"], str)
        self.assertIsInstance(result["age"], int)

    def test_enum(self):
        schema = {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["red", "green", "blue"]},
            },
        }
        result = json.loads(_generate_structured(schema))
        self.assertEqual(result["color"], "red")

    def test_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "active": {"type": "boolean"},
                    },
                },
            },
        }
        result = json.loads(_generate_structured(schema))
        self.assertIsInstance(result["user"]["id"], int)
        self.assertIsInstance(result["user"]["active"], bool)

    def test_array(self):
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}},
            },
        }
        result = json.loads(_generate_structured(schema))
        self.assertIsInstance(result["items"], list)

    def test_none_schema(self):
        result = json.loads(_generate_structured(None))
        self.assertIn("result", result)


class TestStructuredOutputEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = start_mock_engine(port=19944)
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, data):
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:19944/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_guided_json(self):
        schema = {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "model": {"type": "string"},
                "year": {"type": "integer"},
            },
        }
        resp = self._post({
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Describe a car"}],
            "guided_json": schema,
        })
        content = resp["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        self.assertIn("brand", parsed)
        self.assertIn("year", parsed)
        self.assertIsInstance(parsed["year"], int)

    def test_response_format_json_schema(self):
        resp = self._post({
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Give me a car"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "car",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "color": {"type": "string", "enum": ["red", "blue"]},
                        },
                    },
                },
            },
        })
        parsed = json.loads(resp["choices"][0]["message"]["content"])
        self.assertEqual(parsed["color"], "red")

    def test_normal_request_unaffected(self):
        resp = self._post({
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        content = resp["choices"][0]["message"]["content"]
        # Should NOT be JSON — it's a normal freetext response
        with self.assertRaises(json.JSONDecodeError):
            json.loads(content)


if __name__ == "__main__":
    unittest.main()
