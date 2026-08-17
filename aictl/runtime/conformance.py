"""aictl engine conformance — does this engine actually do what aictl needs?

First-principles gap this closes: every value aictl delivers rests on an
inference engine behaving the way aictl expects, yet nothing verified that.
`aictl selftest` never contacts an engine at all, and the whole test suite +
`aictl demo` exercise only the bundled mock engine. So a user's first signal
that (say) their engine has no `/v1/embeddings` was a silent quality
degradation mid-request: RAG and the semantic cache fall back to the
non-semantic SHA-256 hash embedding, and retrieval quietly stops being
semantic.

`check_conformance(endpoint)` probes the handful of HTTP surfaces aictl
actually depends on and maps each result to *which aictl features work,
degrade, or break*. That mapping is the point — a bare pass/fail table would
just be another status command (see docs/REVIEW_v1.7.0.md on observability
sprawl); what a user needs is "what does this mean for me".

Read-only and non-destructive: every probe is a GET, or a POST with a
minimal 1-token payload. Nothing here is on a serving path — it runs only
when a user asks. Every probe failure is captured, never raised: an engine
that is down must still produce a full report.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from aictl.core.constants import CONFORMANCE_PROBE_TIMEOUT

# Severity of a failed probe:
#   "required" — core aictl functionality is broken without it
#   "degraded" — aictl still works, but a named feature silently loses quality
#   "optional" — nice to have; absence costs a specific advisory feature only
REQUIRED = "required"
DEGRADED = "degraded"
OPTIONAL = "optional"
#   "insecure" — the engine works, but the transport exposes credentials and
#   content. Deliberately a fourth severity rather than reusing "degraded":
#   nothing about output quality changes, so calling it degraded would
#   misdescribe it, and calling it required would report a working engine as
#   broken. It does count against conformance — a deployment shipping API keys
#   in cleartext is not production-conformant, whatever its response quality.
INSECURE = "insecure"

# Hosts where plaintext HTTP never leaves the machine, so there is nothing on
# the wire to intercept. aictl's own defaults live here, and flagging them
# would make the check noise on every local deployment.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", ""})


@dataclass
class ProbeResult:
    """One probed surface, plus what its absence costs the user."""
    name: str
    path: str
    ok: bool
    severity: str            # REQUIRED | DEGRADED | OPTIONAL | INSECURE
    detail: str = ""         # short human explanation (error text, or what was seen)
    powers: list[str] = field(default_factory=list)   # aictl features relying on it
    impact: str = ""         # what happens when ok is False


@dataclass
class ConformanceReport:
    endpoint: str
    reachable: bool
    probes: list[ProbeResult] = field(default_factory=list)

    @property
    def failed_required(self) -> list[ProbeResult]:
        return [p for p in self.probes if not p.ok and p.severity == REQUIRED]

    @property
    def failed_degraded(self) -> list[ProbeResult]:
        return [p for p in self.probes if not p.ok and p.severity == DEGRADED]

    @property
    def failed_insecure(self) -> list[ProbeResult]:
        return [p for p in self.probes if not p.ok and p.severity == INSECURE]

    @property
    def conformant(self) -> bool:
        """True when nothing required, quality-affecting, or insecure failed."""
        return (not self.failed_required and not self.failed_degraded
                and not self.failed_insecure)

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "reachable": self.reachable,
            "conformant": self.conformant,
            "probes": [
                {
                    "name": p.name,
                    "path": p.path,
                    "ok": p.ok,
                    "severity": p.severity,
                    "detail": p.detail,
                    "powers": p.powers,
                    "impact": p.impact,
                }
                for p in self.probes
            ],
        }


def _get(url: str, timeout: float) -> tuple[bool, str, bytes]:
    """GET a URL. Returns (ok, detail, body). Never raises."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return True, f"HTTP {resp.status}", body
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}", b""
    except Exception as e:
        return False, str(e)[:80], b""


def _post_json(url: str, payload: dict[str, Any], timeout: float,
               stream: bool = False) -> tuple[bool, str, bytes]:
    """POST JSON. Returns (ok, detail, body). Never raises.

    For `stream=True` only the first chunk is read — enough to confirm the
    server actually emits SSE frames without draining a whole generation.
    """
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096) if stream else resp.read()
            return True, f"HTTP {resp.status}", body
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}", b""
    except Exception as e:
        return False, str(e)[:80], b""


def check_conformance(endpoint: str, timeout: float | None = None,
                      model: str = "") -> ConformanceReport:
    """Probe `endpoint` for the HTTP surfaces aictl depends on.

    `model` is the model name sent in chat/embedding probes; engines that
    require a valid name (vLLM) get the first one advertised by /v1/models
    when the caller doesn't specify one. Never raises — an unreachable or
    hostile endpoint still yields a complete report.
    """
    t = timeout if timeout is not None else CONFORMANCE_PROBE_TIMEOUT
    base = endpoint.rstrip("/")
    report = ConformanceReport(endpoint=base, reachable=False)

    if urlparse(base).scheme not in ("http", "https"):
        report.probes.append(ProbeResult(
            name="endpoint", path="", ok=False, severity=REQUIRED,
            detail=f"not an http(s) URL: {endpoint!r}",
            powers=["everything"], impact="aictl cannot talk to this endpoint at all",
        ))
        return report

    # 0. Transport security. Checked first because it needs no network call
    #    and, unlike everything below, a failure here is not something the
    #    engine can answer for — it is a property of how it was addressed.
    parsed = urlparse(base)
    host = (parsed.hostname or "").lower()
    plaintext_remote = parsed.scheme == "http" and host not in _LOOPBACK_HOSTS
    report.probes.append(ProbeResult(
        name="transport", path=base.split("://")[0] + "://", ok=not plaintext_remote,
        severity=INSECURE,
        detail=("plaintext HTTP to a non-loopback host" if plaintext_remote
                else ("TLS" if parsed.scheme == "https" else "plaintext, but loopback only")),
        powers=["API-key confidentiality", "prompt and completion privacy"],
        impact=("the Authorization header and every prompt and completion cross "
                "the network in cleartext — anything on the path can read the "
                "API key and the traffic"),
    ))

    # 1. Model listing — the discovery surface every adapter and the
    #    embedding-model detector depend on.
    models_ok, models_detail, models_body = _get(f"{base}/v1/models", t)
    discovered: list[str] = []
    if models_ok:
        try:
            data = json.loads(models_body)
            discovered = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
            models_detail = f"{len(discovered)} model(s)"
        except Exception:
            models_ok = False
            models_detail = "malformed JSON"
    report.probes.append(ProbeResult(
        name="model listing", path="/v1/models", ok=models_ok, severity=REQUIRED,
        detail=models_detail,
        powers=["engines list/health", "model auto-selection",
                "embedding-model capability detection"],
        impact="aictl cannot enumerate models; embedding detection falls back to none",
    ))

    # 2. Reachability — /health if present, else the /v1/models result above.
    health_ok, health_detail, _ = _get(f"{base}/health", t)
    reachable = health_ok or models_ok
    report.reachable = reachable
    report.probes.append(ProbeResult(
        name="reachability", path="/health", ok=reachable, severity=REQUIRED,
        detail=health_detail if health_ok else (
            "no /health, but /v1/models answered" if models_ok else health_detail),
        powers=["everything"],
        impact="engine is unreachable; every aictl operation against it fails",
    ))
    if not reachable:
        # Still emit the remaining probes as failed-unknown so the report shape
        # is stable for --json consumers, rather than silently truncating.
        for name, path, sev, powers, impact in (
            ("chat completions", "/v1/chat/completions", REQUIRED,
             ["ai.ask", "chat", "route", "cascade", "eval", "guard model-check"],
             "no inference is possible"),
            ("streaming", "/v1/chat/completions (stream)", OPTIONAL,
             ["streaming chat responses"], "responses arrive only as one block"),
            ("embeddings", "/v1/embeddings", DEGRADED,
             ["rag", "semantic cache", "route --knn", "reranker input"],
             "falls back to the non-semantic hash embedding"),
            ("metrics", "/metrics", OPTIONAL,
             ["SLO governor", "autoscaler", "capacity"],
             "SLO/autoscaling decisions run without engine telemetry"),
        ):
            report.probes.append(ProbeResult(
                name=name, path=path, ok=False, severity=sev,
                detail="skipped (engine unreachable)", powers=powers, impact=impact))
        return report

    probe_model = model or (discovered[0] if discovered else "test-model")

    # 3. Chat completions — the core inference surface.
    chat_payload = {
        "model": probe_model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    chat_ok, chat_detail, chat_body = _post_json(
        f"{base}/v1/chat/completions", chat_payload, t)
    if chat_ok:
        try:
            parsed = json.loads(chat_body)
            if "choices" not in parsed:
                chat_ok = False
                chat_detail = "response has no 'choices' field"
        except Exception:
            chat_ok = False
            chat_detail = "malformed JSON"
    elif not model and not discovered:
        # The model name was invented because /v1/models gave us nothing, and
        # engines that validate the name (vLLM) reject an unknown one. Calling
        # that a broken chat endpoint would be a false negative — the endpoint
        # may be fine and only the name wrong. Say which we actually know.
        chat_detail = (f"{chat_detail} — probed with a guessed model name "
                       f"({probe_model!r}) because /v1/models did not answer; "
                       "retry with --model to distinguish a broken endpoint "
                       "from a wrong name")
    report.probes.append(ProbeResult(
        name="chat completions", path="/v1/chat/completions", ok=chat_ok,
        severity=REQUIRED, detail=chat_detail,
        powers=["ai.ask", "chat", "route", "cascade", "eval", "guard model-check"],
        impact="no inference is possible through aictl",
    ))

    # 4. Streaming — optional; only affects response delivery, not capability.
    stream_ok, stream_detail, stream_body = _post_json(
        f"{base}/v1/chat/completions", {**chat_payload, "stream": True}, t, stream=True)
    if stream_ok and b"data:" not in stream_body:
        stream_ok = False
        stream_detail = "accepted stream=true but did not emit SSE 'data:' frames"
    report.probes.append(ProbeResult(
        name="streaming", path="/v1/chat/completions (stream)", ok=stream_ok,
        severity=OPTIONAL, detail=stream_detail,
        powers=["streaming chat responses"],
        impact="responses arrive only as one block (functionality unaffected)",
    ))

    # 5. Embeddings — the silent-degradation surface this whole module exists for.
    emb_ok, emb_detail, emb_body = _post_json(
        f"{base}/v1/embeddings", {"model": probe_model, "input": ["ping"]}, t)
    if emb_ok:
        try:
            vectors = json.loads(emb_body).get("data", [])
            dim = len(vectors[0].get("embedding", [])) if vectors else 0
            if dim == 0:
                emb_ok = False
                emb_detail = "response contained no embedding vector"
            else:
                emb_detail = f"{dim}-dim vector"
        except Exception:
            emb_ok = False
            emb_detail = "malformed JSON"
    report.probes.append(ProbeResult(
        name="embeddings", path="/v1/embeddings", ok=emb_ok, severity=DEGRADED,
        detail=emb_detail,
        powers=["rag index/search/ask", "semantic cache", "route --knn",
                "reranker candidate pool"],
        impact=("retrieval and caching fall back to the non-semantic hash embedding "
                "— exact-match hits only, no semantic similarity"),
    ))

    # 6. Prometheus metrics — powers SLO/autoscaling advice.
    metrics_ok, metrics_detail, _ = _get(f"{base}/metrics", t)
    report.probes.append(ProbeResult(
        name="metrics", path="/metrics", ok=metrics_ok, severity=OPTIONAL,
        detail=metrics_detail,
        powers=["SLO governor", "autoscaler", "capacity planning"],
        impact="SLO and autoscaling decisions run without engine telemetry",
    ))

    return report
