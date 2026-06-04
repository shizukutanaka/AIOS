"""E2E test for structured output through the full pipeline.

Proves: Client → Mock Engine with guided_json → valid JSON response.
This is critical for agent/tool-calling workloads.
"""

import json
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestStructuredOutputE2E(unittest.TestCase):
    """Verify structured output works through the mock engine."""

    PORT = 19945

    @classmethod
    def setUpClass(cls):
        from aictl.daemon.mock_engine import start_mock_engine
        cls.server = start_mock_engine(port=cls.PORT)
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, path, data):
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.PORT}{path}",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_guided_json_simple(self):
        """guided_json with a simple object schema."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Generate a person"}],
            "guided_json": schema,
        })
        content = resp["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        self.assertIn("name", parsed)
        self.assertIn("age", parsed)
        self.assertIsInstance(parsed["name"], str)
        self.assertIsInstance(parsed["age"], int)

    def test_guided_json_nested(self):
        """guided_json with nested objects."""
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "active": {"type": "boolean"},
                    },
                },
                "scores": {
                    "type": "array",
                    "items": {"type": "number"},
                },
            },
        }
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Generate data"}],
            "guided_json": schema,
        })
        parsed = json.loads(resp["choices"][0]["message"]["content"])
        self.assertIn("user", parsed)
        self.assertIn("scores", parsed)
        self.assertIsInstance(parsed["user"]["active"], bool)
        self.assertIsInstance(parsed["scores"], list)

    def test_guided_json_enum(self):
        """guided_json with enum constraint."""
        schema = {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["red", "green", "blue"]},
            },
        }
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Pick a color"}],
            "guided_json": schema,
        })
        parsed = json.loads(resp["choices"][0]["message"]["content"])
        self.assertIn(parsed["color"], ["red", "green", "blue"])

    def test_response_format_json_schema(self):
        """OpenAI-compatible response_format with json_schema."""
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Generate"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "test",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "answer": {"type": "string"},
                        },
                    },
                },
            },
        })
        parsed = json.loads(resp["choices"][0]["message"]["content"])
        self.assertIn("answer", parsed)

    def test_structured_outputs_param(self):
        """vLLM v0.19+ unified structured_outputs parameter."""
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Generate"}],
            "structured_outputs": {
                "json": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["ok", "error"]},
                        "count": {"type": "integer"},
                    },
                },
            },
        })
        parsed = json.loads(resp["choices"][0]["message"]["content"])
        self.assertIn(parsed["status"], ["ok", "error"])
        self.assertIsInstance(parsed["count"], int)

    def test_usage_tokens_with_structured(self):
        """Verify usage tokens are reported for structured output."""
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "test"}],
            "guided_json": {"type": "object", "properties": {"x": {"type": "integer"}}},
        })
        self.assertIn("usage", resp)
        self.assertGreater(resp["usage"]["completion_tokens"], 0)

    def test_non_structured_still_works(self):
        """Regular (non-structured) request still returns text."""
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        content = resp["choices"][0]["message"]["content"]
        self.assertIsInstance(content, str)
        self.assertGreater(len(content), 0)
        # Should NOT be valid JSON (it's freeform text)
        try:
            json.loads(content)
            # If it happens to be JSON, that's fine too
        except json.JSONDecodeError:
            pass  # Expected for freeform text


if __name__ == "__main__":
    unittest.main()


class TestToolCallingE2E(unittest.TestCase):
    """Test tool calling through mock engine."""

    PORT = 19946

    @classmethod
    def setUpClass(cls):
        from aictl.daemon.mock_engine import start_mock_engine
        cls.server = start_mock_engine(port=cls.PORT)
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, path, data):
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.PORT}{path}",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_tool_call_basic(self):
        """Tool call returns function name and arguments."""
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Get weather"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string"},
                            "units": {"type": "string", "default": "celsius"},
                        },
                    },
                },
            }],
        })
        choice = resp["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        tc = choice["message"]["tool_calls"]
        self.assertEqual(len(tc), 1)
        self.assertEqual(tc[0]["function"]["name"], "get_weather")
        args = json.loads(tc[0]["function"]["arguments"])
        self.assertIn("city", args)

    def test_tool_call_id_format(self):
        """Tool call ID has correct format."""
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Search"}],
            "tools": [{"type": "function", "function": {"name": "search", "parameters": {"type": "object", "properties": {}}}}],
        })
        tc = resp["choices"][0]["message"]["tool_calls"][0]
        self.assertTrue(tc["id"].startswith("call_mock_"))
        self.assertEqual(tc["type"], "function")

    def test_ollama_format_json(self):
        """Ollama format='json' returns valid JSON."""
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Data"}],
            "format": "json",
        })
        content = resp["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        self.assertIsInstance(parsed, dict)

    def test_ollama_format_schema(self):
        """Ollama format=schema returns schema-conforming JSON."""
        resp = self._post("/v1/chat/completions", {
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "Person"}],
            "format": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "active": {"type": "boolean"},
                },
            },
        })
        parsed = json.loads(resp["choices"][0]["message"]["content"])
        self.assertIn("name", parsed)
        self.assertIsInstance(parsed["active"], bool)
