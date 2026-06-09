"""Completions proxy: OpenAI-compatible API that routes through the broker.

Listens on a single port, accepts /v1/chat/completions and /v1/completions,
routes to the best available engine via the Runtime Broker, and streams
the response back to the client. This gives users ONE stable endpoint
regardless of which engine is actually serving.

Port: 8080 (configurable)
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

from aictl.runtime.router import BrokerRouter, RouteRequest
from aictl.core.config import load_config
from aictl.core.state import StateStore
from aictl.core.constants import PROXY_PORT, DAEMON_HOST


class ProxyHandler(BaseHTTPRequestHandler):
    store: StateStore
    router: BrokerRouter | None = None

    def log_message(self, fmt: Any, *args: Any) -> None:
        """Log message."""
        pass

    def _get_router(self) -> BrokerRouter:
        """Retrieve and return the requested value."""
        if ProxyHandler.router is None:
            config = load_config(self.store.dir)
            ProxyHandler.router = BrokerRouter(endpoints=config.engines.to_dict())
        return ProxyHandler.router

    def do_POST(self) -> None:
        """Do post."""
        path = self.path.rstrip("/")

        # API key authentication (if keys are configured)
        if not self._check_auth():
            return

        if path in ("/v1/chat/completions", "/v1/completions"):
            self._proxy_completion()
        elif path == "/v1/embeddings":
            self._proxy_embedding()
        else:
            self._error(404, "Not found")

    def do_GET(self) -> None:
        """Do get."""
        path = self.path.rstrip("/")
        if path == "/v1/models":
            self._list_models()
        elif path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._error(404, "Not found")

    def _check_auth(self) -> bool:
        """Validate API key from Authorization header. Returns True if OK."""
        from aictl.core.apikeys import KeyManager
        mgr = KeyManager(self.store.dir if self.store else None)
        keys = mgr.list_keys()

        # If no keys configured, allow all (open mode)
        if not keys:
            return True

        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._error(401, "Missing API key", {"hint": "Set Authorization: Bearer aios-..."})
            return False

        raw_key = auth[7:]
        valid, reason, key = mgr.validate(raw_key)
        if not valid or key is None:
            self._error(403, f"Invalid API key: {reason}")
            return False

        # Rate limit check
        ok, msg = mgr.check_rate_limit(key)
        if not ok:
            self._error(429, msg)
            return False

        # Record usage
        mgr.record_usage(key.key_id)

        # Audit
        from aictl.core.audit import audit
        audit("proxy.request", resource=key.name, action="inference",
              state_dir=self.store.dir if self.store else None,
              key_id=key.key_id)

        return True

    def do_OPTIONS(self) -> None:
        """Do options."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _proxy_completion(self) -> None:
        """Proxy a completion request to the upstream engine."""
        body = self._read_body()
        model = body.get("model", "")
        start_ns = time.time_ns()

        # Route
        router = self._get_router()
        decision = router.route(RouteRequest(model=model, objective="balanced"))

        if not decision.endpoint:
            # Try cloud fallback
            result = self._try_cloud_fallback(body)
            if result:
                return
            self._error(503, "No available engine (local or cloud)",
                        {"reason_codes": decision.reason_codes})
            return

        # Forward to selected engine
        target_url = f"{decision.endpoint.rstrip('/')}{self.path}"
        stream = body.get("stream", False)

        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(
                target_url, data=data,
                headers={"Content-Type": "application/json"},
            )

            with urllib.request.urlopen(req, timeout=120) as resp:
                if stream:
                    self._stream_response(resp, decision)
                else:
                    result = resp.read()
                    end_ns = time.time_ns()

                    # Token metering
                    self._meter_tokens(body, result)

                    # GenAI span
                    self._record_genai_span(body, result, decision, start_ns, end_ns)

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("X-AIOS-Engine", decision.selected_engine)
                    self.send_header("X-AIOS-Score", str(decision.score))
                    self.end_headers()
                    self.wfile.write(result)

        except urllib.error.HTTPError as e:
            # Preserve the upstream error body (e.g. vLLM's validation message)
            # so OpenAI-compatible clients can surface the real cause.
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            msg = f"Upstream error from {decision.selected_engine}"
            if detail:
                msg += f": {detail}"
            self._error(e.code, msg)
        except Exception as e:
            self._error(502, f"Proxy error: {e}")

    def _stream_response(self, resp: Any, decision: Any) -> None:
        """Stream the response back to the client as SSE."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-AIOS-Engine", decision.selected_engine)
        self.end_headers()

        try:
            for chunk in iter(lambda: resp.read(4096), b""):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # best-effort; failure is non-critical

    def _proxy_embedding(self) -> None:
        """Proxy an embedding request to the upstream engine."""
        body = self._read_body()
        model = body.get("model", "")

        router = self._get_router()
        decision = router.route(RouteRequest(model=model, objective="throughput"))

        if not decision.endpoint:
            self._error(503, "No available engine")
            return

        target_url = f"{decision.endpoint.rstrip('/')}/v1/embeddings"
        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(target_url, data=data,
                                        headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = resp.read()
                self._raw_response(200, result)
        except Exception as e:
            self._error(502, f"Proxy error: {e}")

    def _list_models(self) -> list[Any]:
        """Aggregate models from all engines."""
        from aictl.runtime.adapters import discover_engines
        config = load_config(self.store.dir)
        healths = discover_engines(config.engines.to_dict())

        models: list[dict[str, Any]] = []
        seen: set[str] = set()
        for h in healths:
            if h.reachable:
                for m in h.models:
                    if m not in seen:
                        seen.add(m)
                        models.append({
                            "id": m,
                            "object": "model",
                            "owned_by": h.engine,
                        })

        self._json(200, {"object": "list", "data": models})

    def _meter_tokens(self, request_body: dict[str, Any], response_bytes: bytes) -> None:
        """Record token usage from a completion response."""
        try:
            resp = json.loads(response_bytes)
            usage = resp.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            if prompt_tokens == 0 and completion_tokens == 0:
                return

            auth = self.headers.get("Authorization", "")
            entity_id = "anonymous"
            if auth.startswith("Bearer ") and auth[7:].startswith("aios-"):
                entity_id = auth[7:20]

            model = request_body.get("model", "unknown")
            from aictl.core.metering import TokenMeter
            meter = TokenMeter(self.store.dir if self.store else None)
            meter.record(entity_id, model, prompt_tokens, completion_tokens)
        except Exception:
            pass  # Metering failures must not affect requests

    def _try_cloud_fallback(self, body: dict[str, Any]) -> bool:
        """Attempt cloud provider fallback. Returns True if successful."""
        try:
            from aictl.runtime.fallback import load_fallback_config, cloud_completion
            config = load_fallback_config(self.store.dir if self.store else None)
            if not config.enabled:
                return False

            messages = body.get("messages", [])
            model = body.get("model", "")
            max_tokens = body.get("max_tokens", 0)

            result = cloud_completion(config, messages, model=model, max_tokens=max_tokens)
            if result is None:
                return False

            # Meter cloud usage
            self._meter_tokens(body, json.dumps(result).encode())

            # Audit cloud fallback
            from aictl.core.audit import AuditLog, AuditEntry
            log = AuditLog(self.store.dir if self.store else None)
            log.write(AuditEntry(
                event="proxy.cloud_fallback",
                resource=result.get("_aios_provider", "unknown"),
            ))

            result_bytes = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-AIOS-Engine", "cloud-fallback")
            self.send_header("X-AIOS-Provider", result.get("_aios_provider", ""))
            self.end_headers()
            self.wfile.write(result_bytes)
            return True
        except Exception:
            return False

    def _record_genai_span(self, request_body: Any, response_bytes: Any, decision: Any, start_ns: Any, end_ns: Any) -> None:
        """Record a GenAI span for OTel export."""
        try:
            from aictl.metrics.genai_spans import span_from_proxy_request
            resp = json.loads(response_bytes)
            span = span_from_proxy_request(
                request_body, resp,
                engine=decision.selected_engine,
                endpoint=decision.endpoint,
                start_ns=start_ns, end_ns=end_ns,
            )
            span.router_score = decision.score

            if self.store:
                spans_path = self.store.dir / "genai_spans.jsonl"
                with open(spans_path, "a") as f:
                    from dataclasses import asdict
                    f.write(json.dumps(asdict(span)) + "\n")
        except Exception:
            pass  # best-effort; failure is non-critical

    def _read_body(self) -> dict[str, Any]:
        """Read and return data from the source."""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _json(self, status: int, data: Any) -> None:
        """Serialize and send a JSON response."""
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _raw_response(self, status: int, body: bytes) -> None:
        """Send a raw HTTP response with body."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, msg: str, extra: dict[str, Any] | None = None) -> None:
        """Build a JSON-RPC 2.0 error response dict."""
        data = {"error": {"message": msg, "type": "aios_proxy_error"}}
        if extra:
            data["error"].update(extra)
        self._json(status, data)


def serve_proxy(host: str = DAEMON_HOST, port: int = PROXY_PORT,
                store: StateStore | None = None) -> None:
    """Start the completions proxy."""
    if store is None:
        store = StateStore()
    ProxyHandler.store = store
    server = HTTPServer((host, port), ProxyHandler)
    print(f"AI OS completions proxy on http://{host}:{port}/v1/chat/completions")
    server.serve_forever()
