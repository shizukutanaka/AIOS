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

_MAX_BODY_BYTES = 100 * 1024 * 1024  # 100 MB cap to prevent memory exhaustion


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

        # Tenant-class rate limit (additional ceiling on top of the per-key
        # limit above) — only applies if this key was explicitly linked to a
        # tenant via `aictl tenant link-key`. An unlinked key behaves exactly
        # as before (per-key limiting only); TenantRateLimiter previously had
        # no caller anywhere in the codebase, so tenant classes were cosmetic.
        from aictl.core.tenant import find_tenant_by_key_id, get_rate_limiter
        tenant = find_tenant_by_key_id(self.store.dir if self.store else None, key.key_id)
        if tenant is not None:
            tenant_class = tenant.get("tenant_class", "standard")
            if not get_rate_limiter().check(tenant["id"], tenant_class):
                self._error(429, f"Tenant '{tenant['id']}' rate limit exceeded "
                                 f"(class: {tenant_class})")
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

        # Model-trust gate: enforce signature policy BEFORE routing/serving.
        allowed, reason = self._model_trust_ok(model)
        if not allowed:
            self._error(403, reason)
            return

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

        # Same model-trust gate as _proxy_completion (Pass 166): an
        # unsigned-model bypass on /v1/embeddings would undercut
        # trust_policy=enforce / tenant require_signed_models entirely —
        # embeddings requests carry the same document content a regulated
        # tenant is trying to keep off untrusted models.
        allowed, reason = self._model_trust_ok(model)
        if not allowed:
            self._error(403, reason)
            return

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

    def _list_models(self) -> None:
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
                # Attribute by the key's id (SHA-256 prefix), NEVER the raw key —
                # using the raw key persisted the secret in plaintext in the
                # metering store and surfaced it in `meter report`. This id matches
                # `apikey list`, so usage still maps cleanly back to a key.
                from aictl.core.apikeys import key_id_for
                entity_id = key_id_for(auth[7:])

            model = request_body.get("model", "unknown")
            from aictl.core.metering import TokenMeter
            meter = TokenMeter(self.store.dir if self.store else None)
            meter.record(entity_id, model, prompt_tokens, completion_tokens)

            # Feed the actual token count back to the tenant-class limiter
            # (checked pre-flight in _check_auth; recorded here once the real
            # count is known — one check()+record() pair per request, matching
            # TenantRateLimiter's own documented two-phase contract).
            from aictl.core.tenant import find_tenant_by_key_id, get_rate_limiter
            tenant = find_tenant_by_key_id(self.store.dir if self.store else None, entity_id)
            if tenant is not None:
                get_rate_limiter().record(tenant["id"], prompt_tokens + completion_tokens)
        except Exception:
            pass  # Metering failures must not affect requests

    def _current_tenant(self) -> dict[str, Any] | None:
        """Resolve the requesting API key's linked tenant record, or None.

        Shared by the internet-egress gate and the model-trust gate — both need
        'which tenant is this request', and neither should duplicate the
        bearer-parse + key_id + registry-lookup dance."""
        auth = self.headers.get("Authorization", "")
        if not (auth.startswith("Bearer ") and auth[7:].startswith("aios-")):
            return None
        from aictl.core.apikeys import key_id_for
        from aictl.core.tenant import find_tenant_by_key_id
        return find_tenant_by_key_id(self.store.dir if self.store else None,
                                     key_id_for(auth[7:]))

    def _audit(self, event: str, resource: str, *, action: str = "deny",
               outcome: str = "blocked", **details: Any) -> None:
        """Best-effort audit write (never raises into the request path)."""
        try:
            from aictl.core.audit import AuditLog, AuditEntry
            AuditLog(self.store.dir if self.store else None).write(AuditEntry(
                event=event, resource=resource, action=action,
                outcome=outcome, details=details))
        except Exception:
            pass

    def _tenant_disallows_internet(self) -> bool:
        """True if the requesting key's linked tenant class has
        allow_internet=False. Unlinked keys are unaffected (opt-in layer,
        matching the tenant rate-limit enforcement added in Pass 164)."""
        from aictl.core.tenant import get_tenant_class
        tenant = self._current_tenant()
        if tenant is None:
            return False
        tc = get_tenant_class(tenant.get("tenant_class", "standard"))
        if tc.allow_internet:
            return False
        self._audit("proxy.cloud_fallback_blocked", tenant["id"],
                    tenant_class=tenant.get("tenant_class", "standard"))
        return True

    def _model_is_signed(self, model: str) -> bool:
        """True if `model` is present in the local registry AND marked signed.

        An unknown model (never registered) is treated as unsigned — unknown
        provenance is not trusted provenance."""
        if not model or not self.store:
            return False
        try:
            for m in self.store.list_models():
                if m.get("name") == model and m.get("signed"):
                    return True
        except Exception:
            return False
        return False

    def _model_trust_ok(self, model: str) -> tuple[bool, str]:
        """Model-trust gate (P1 global trust_policy + P2 tenant
        require_signed_models). Returns (allowed, reason).

        Strictness resolution (strictest wins):
          - tenant.require_signed_models True  -> STRICT (block unsigned)
          - global trust_policy == 'enforce'   -> STRICT
          - global trust_policy == 'disabled'  -> allow, no check
          - otherwise ('warn', default)        -> allow, audit an unsigned warning

        A signed model always passes. Enforcement is opt-in: default config is
        'warn' and no tenant requires signing, so out-of-the-box behavior is
        unchanged — nothing is ever blocked until an operator asks for it."""
        from aictl.core.config import load_config
        from aictl.core.tenant import get_tenant_class

        policy = load_config(self.store.dir if self.store else None).trust_policy
        tenant = self._current_tenant()
        tenant_requires = (
            tenant is not None
            and get_tenant_class(tenant.get("tenant_class", "standard")).require_signed_models
        )

        strict = tenant_requires or policy == "enforce"
        if not strict and policy == "disabled":
            return True, ""
        if self._model_is_signed(model):
            return True, ""
        if strict:
            who = f"tenant '{tenant['id']}'" if tenant_requires else "trust_policy=enforce"
            self._audit("proxy.unsigned_model_blocked", model or "(none)",
                        reason=who)
            return False, (f"Model '{model}' is not a signed/verified model and "
                           f"{who} requires signed models. Register + verify it "
                           f"(aictl model verify {model}) or relax the policy.")
        # warn mode: allow, but leave an audit trail of the unsigned serve
        self._audit("proxy.unsigned_model_served", model or "(none)",
                    action="allow", outcome="warning")
        return True, ""

    def _try_cloud_fallback(self, body: dict[str, Any]) -> bool:
        """Attempt cloud provider fallback. Returns True if successful."""
        try:
            # A tenant whose class disallows internet egress must NEVER be
            # routed to an external cloud API — even when local engines are
            # down and fallback is globally enabled. `allow_internet` (like
            # max_requests_per_min before Pass 164) had no runtime consumer
            # anywhere in the codebase; a regulated/air-gapped tenant would
            # otherwise silently leak its request to a cloud provider the
            # moment local engines became unreachable.
            if self._tenant_disallows_internet():
                return False

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
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            length = 0
        # Must be `<= 0`, not `== 0`: a NEGATIVE Content-Length survives the
        # `min(length, _MAX_BODY_BYTES)` cap (min(-5, cap) == -5) and then
        # `rfile.read(-5)` reads until EOF — defeating the 100 MB memory-
        # exhaustion guard this cap exists to enforce. Treat any non-positive
        # length as "no body" (matches aiosd._read_body).
        if length <= 0:
            return {}
        length = min(length, _MAX_BODY_BYTES)
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
