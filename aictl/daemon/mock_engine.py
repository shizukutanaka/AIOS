"""Mock inference engine: OpenAI-compatible server for demo and testing.

Provides all endpoints that a real vLLM/SGLang/Ollama would expose:
  GET  /health              → {"status": "ok"}
  GET  /v1/models           → model list
  POST /v1/chat/completions → streaming or non-streaming response
  GET  /metrics             → Prometheus-compatible metrics

Responses are deterministic (seeded by prompt hash) so tests are reproducible.
"""

from __future__ import annotations

import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any

from aictl.core.constants import AICTL_VERSION

# Mock model catalog
MOCK_MODELS = [
    {"id": "mock-llama3-8b", "object": "model", "owned_by": "aios-mock"},
    {"id": "mock-qwen2.5-7b", "object": "model", "owned_by": "aios-mock"},
]

# Canned responses keyed by first word of prompt
RESPONSES = {
    "hello": "Hello! I'm a mock inference engine running inside aictl. How can I help you today?",
    "what": "That's a great question. As a mock engine, I generate deterministic responses for testing the full aictl stack — daemon, proxy, router, and SLO governor.",
    "test": "Test confirmed. The mock engine is working correctly. All systems operational.",
    "default": f"This is a response from the aictl mock inference engine (v{AICTL_VERSION}). It demonstrates that the full request path works: client → proxy → router → engine → response.",
}

# Metrics state
_metrics = {
    "requests_total": 0,
    "tokens_generated": 0,
    "active_requests": 0,
    "queue_depth": 0,
    "kv_cache_usage": 0.0,
    "ttft_sum_ms": 0.0,
}
_lock = threading.Lock()


class MockEngineHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible mock inference handler."""

    # Cap the request body the mock will read (it only ever needs a small JSON
    # payload). Matches the hardening in aiosd / proxy.
    _MAX_BODY_BYTES = 10 * 1024 * 1024

    def log_message(self, format: Any, *args: Any) -> None:
        """Log message."""
        pass  # Silence request logs

    def _read_json_body(self) -> dict[str, Any]:
        """Read and JSON-decode the request body, robust to bad headers.

        A malformed Content-Length must not crash the handler thread (a bare
        `int(...)` would raise), and a NEGATIVE length must be treated as "no
        body" — otherwise `rfile.read(-1)` reads until EOF, defeating the size
        cap (the same trap fixed in proxy._read_body). Returns {} on any
        non-positive/unparseable length or malformed JSON.
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return {}
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(min(length, self._MAX_BODY_BYTES)))
        except (ValueError, OSError):
            return {}

    def do_GET(self) -> None:
        """Do get."""
        path = self.path.split("?")[0].rstrip("/")

        if path == "/health":
            self._json({"status": "ok"})
        elif path == "/v1/models":
            self._json({"object": "list", "data": MOCK_MODELS})
        elif path == "/metrics":
            self._prometheus_metrics()
        elif path == "/api/tags":
            # Ollama compatibility
            self._json({"models": [
                {"name": m["id"], "size": 4_000_000_000}
                for m in MOCK_MODELS
            ]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        """Do post."""
        path = self.path.split("?")[0].rstrip("/")

        if path == "/v1/chat/completions":
            self._chat_completions()
        elif path == "/api/generate":
            # Ollama compatibility
            self._ollama_generate()
        else:
            self._json({"error": "not found"}, 404)

    def _chat_completions(self) -> None:
        """Handle POST /v1/chat/completions requests."""
        body = self._read_json_body()

        model = body.get("model", "mock-llama3-8b")
        messages = body.get("messages", [])
        stream = body.get("stream", False)
        max_tokens = min(body.get("max_tokens", 100), 200)

        # Extract prompt
        prompt = ""
        for msg in messages:
            if msg.get("role") == "user":
                prompt = msg.get("content", "")

        # Generate response
        guided_json = body.get("guided_json") or body.get("extra_body", {}).get("guided_json")
        response_format = body.get("response_format", {})
        structured = body.get("structured_outputs", {})
        ollama_format = body.get("format")  # Ollama structured output
        tools = body.get("tools", [])

        # Determine schema source
        schema = None
        if guided_json:
            schema = guided_json
        elif response_format.get("type") == "json_schema":
            schema = response_format.get("json_schema", {}).get("schema")
        elif structured.get("json"):
            schema = structured.get("json")
        elif isinstance(ollama_format, dict):
            schema = ollama_format  # Ollama passes JSON schema directly
        elif ollama_format == "json":
            schema = {"type": "object", "properties": {"response": {"type": "string"}}}

        # Tool calling support
        if tools and not schema:
            response_text, tool_calls = _generate_tool_call(tools, prompt)
            tokens = len(response_text.split()) + sum(len(json.dumps(tc).split()) for tc in tool_calls)
        elif schema:
            response_text = _generate_structured(schema)
            tool_calls = []
            tokens = len(response_text.split())
        else:
            response_text = _generate_response(prompt, max_tokens)
            tool_calls = []
            tokens = len(response_text.split())

        with _lock:
            _metrics["requests_total"] += 1
            _metrics["tokens_generated"] += tokens

        if stream:
            self._stream_response(model, response_text)
        else:
            msg = {"role": "assistant", "content": response_text}
            finish = "stop"
            if tool_calls:
                msg["tool_calls"] = tool_calls
                finish = "tool_calls"
            self._json({
                "id": f"mock-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": msg,
                    "finish_reason": finish,
                }],
                "usage": {
                    "prompt_tokens": sum(len(m.get("content", "").split()) for m in messages),
                    "completion_tokens": tokens,
                    "total_tokens": tokens + sum(len(m.get("content", "").split()) for m in messages),
                },
            })

    def _stream_response(self, model: str, text: str) -> None:
        """SSE streaming response."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        words = text.split()
        for i, word in enumerate(words):
            chunk = {
                "id": f"mock-{int(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": word + (" " if i < len(words) - 1 else "")},
                    "finish_reason": None if i < len(words) - 1 else "stop",
                }],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.02)  # Simulate token generation latency

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _ollama_generate(self) -> None:
        """Handle POST /api/generate (Ollama format) requests."""
        body = self._read_json_body()
        prompt = body.get("prompt", "")
        response_text = _generate_response(prompt, 100)

        self._json({
            "model": body.get("model", "mock"),
            "response": response_text,
            "done": True,
        })

    def _prometheus_metrics(self) -> None:
        """Return Prometheus-format metrics for the mock engine."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        with _lock:
            lines = [
                f'vllm:num_requests_running {_metrics["active_requests"]}',
                f'vllm:num_requests_waiting {_metrics["queue_depth"]}',
                f'vllm:kv_cache_usage_perc {_metrics["kv_cache_usage"]:.4f}',
                f'vllm:num_generation_tokens_total {_metrics["tokens_generated"]}',
                f'vllm:request_success_total {_metrics["requests_total"]}',
            ]
        self.wfile.write(("\n".join(lines) + "\n").encode())

    def _json(self, data: Any, status: int = 200) -> None:
        """Serialize and send a JSON response."""
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _generate_response(prompt: str, max_tokens: int) -> str:
    """Generate a deterministic response based on prompt."""
    words = prompt.strip().split()
    if not words:
        return RESPONSES["default"]

    first_word = words[0].lower().rstrip(".,!?")
    text = RESPONSES.get(first_word, RESPONSES["default"])

    # Truncate to approximate max_tokens
    words = text.split()[:max_tokens]
    return " ".join(words)


def _generate_tool_call(tools: list[Any], prompt: str) -> tuple[str, list[Any]]:
    """Generate a mock tool call response.

    Returns (content_text, tool_calls_list) matching OpenAI format.
    Picks the first tool and generates mock arguments from its parameters.
    """
    if not tools:
        return "No tools available.", []

    tool = tools[0]
    func = tool.get("function", tool)
    name = func.get("name", "unknown")
    params = func.get("parameters", {}).get("properties", {})

    # Generate mock arguments
    args = {}
    for k, v in params.items():
        t = v.get("type", "string")
        if t == "string":
            args[k] = v.get("default", f"mock_{k}")
        elif t == "integer":
            args[k] = v.get("default", 42)
        elif t == "number":
            args[k] = v.get("default", 3.14)
        elif t == "boolean":
            args[k] = v.get("default", True)
        else:
            args[k] = f"mock_{k}"

    tool_calls = [{
        "id": f"call_mock_{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }]

    return "", tool_calls


def _generate_structured(schema: dict[str, Any] | None) -> str:
    """Generate JSON conforming to a JSON schema.

    Supports: string, number, integer, boolean, array, object, enum.
    Used for testing structured output / guided decoding pipelines.
    """
    if not schema or not isinstance(schema, dict):
        return json.dumps({"result": "mock structured output"})

    def _gen(s: dict[str, Any]) -> Any:
        """Execute gen."""
        t = s.get("type", "string")
        if "enum" in s:
            return s["enum"][0] if s["enum"] else ""
        if t == "string":
            return s.get("default", "mock_value")
        if t == "number":
            return s.get("default", 42.0)
        if t == "integer":
            return s.get("default", 42)
        if t == "boolean":
            return s.get("default", True)
        if t == "array":
            items = s.get("items", {"type": "string"})
            return [_gen(items)]
        if t == "object":
            obj = {}
            for prop, prop_schema in s.get("properties", {}).items():
                obj[prop] = _gen(prop_schema)
            return obj
        return "mock"

    return json.dumps(_gen(schema))


class ThreadedMockServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def start_mock_engine(port: int = 9999,
                      host: str = "127.0.0.1") -> ThreadedMockServer:
    """Start a mock engine in a background thread. Returns server instance.

    `host` defaults to loopback and should stay that way for local use: this
    engine answers every request without authentication, so binding it to all
    interfaces is a deliberate act. Containers pass 0.0.0.0 explicitly, because
    inside a container loopback is the container's own and a published port
    reaches nothing.
    """
    server = ThreadedMockServer((host, port), MockEngineHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


