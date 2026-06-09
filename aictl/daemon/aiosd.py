"""aiosd — AI OS local control daemon.

A lightweight HTTP server exposing the control plane API locally.
Binds to 127.0.0.1:7700 by default. Serves:
  - Node status and health
  - Stack CRUD
  - Runtime broker queries
  - Metrics collection
  - Model registry
  - Upgrade planning

Uses only stdlib (http.server) — no Flask/FastAPI dependency for MVP.
"""

from __future__ import annotations

import json
import signal
import time
import threading
from dataclasses import asdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aictl.core.state import StateStore, StackEntry
from aictl.runtime.broker import full_detect, RuntimeReport
from aictl.stack.manifest import parse_file, get_recipe, list_recipes, StackParseError
from aictl.stack.orchestrator import apply_stack, stop_stack, list_running
from aictl.metrics.slo import read_psi, InferenceMetrics, SLOTarget, check_slo


from aictl.core.constants import DAEMON_HOST, DAEMON_PORT

DEFAULT_HOST = DAEMON_HOST
DEFAULT_PORT = DAEMON_PORT


class AIOSHandler(BaseHTTPRequestHandler):
    """HTTP request handler for aiosd."""

    store: StateStore
    _report_cache: RuntimeReport | None = None
    _report_ts: float = 0.0
    _report_lock: threading.Lock = threading.Lock()

    def log_message(self, format: Any, *args: Any) -> None:
        """Log message."""
        # Suppress default access logs; use structured logging later
        pass

    def _json_response(self, data: Any, status: int = 200) -> None:
        """Return a JSON HTTP response with the given status and body dict."""
        body = json.dumps(data, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        """Read and return data from the source."""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw)

    def _get_report(self) -> RuntimeReport:
        """Retrieve and return the requested value."""
        now = time.time()
        with AIOSHandler._report_lock:
            if self._report_cache is None or now - self._report_ts > 30:
                AIOSHandler._report_cache = full_detect()
                AIOSHandler._report_ts = now
            return self._report_cache  # type: ignore

    # ── Routing ─────────────────────────────────────────

    def do_GET(self) -> None:
        """Do get."""
        path = urlparse(self.path).path.rstrip("/")
        routes = {
            "/v1/health": self._health,
            "/v1/node": self._node_status,
            "/v1/runtime": self._runtime_info,
            "/v1/stacks": self._list_stacks,
            "/v1/services": self._list_services,
            "/v1/models": self._list_models,
            "/v1/recipes": self._list_recipes,
            "/v1/metrics/slo": self._slo_status,
            "/v1/metrics/psi": self._psi_status,
            "/v1/upgrade/plan": self._upgrade_plan,
            "/v1/broker/engines": self._broker_engines,
            "/v1/broker/governor": self._broker_governor,
            "/v1/cluster": self._cluster_status,
            "/metrics": self._prometheus_metrics,
            "/v1/events": self._events,
            "/v1/fabric": self._fabric_info,
            "/v1/context": self._context_list,
            "/v1/recommend": self._recommend,
            "/v1/apikeys": self._apikeys_list,
            "/v1/audit": self._audit_recent,
            "/v1/dynamo": self._dynamo_status,
            "/v1/metering": self._metering_status,
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._json_response({"error": "not found", "path": path}, 404)

    def do_POST(self) -> None:
        """Do post."""
        path = urlparse(self.path).path.rstrip("/")
        routes = {
            "/v1/stacks/apply": self._apply_stack,
            "/v1/stacks/down": self._down_stack,
            "/v1/recipes/run": self._run_recipe,
            "/v1/models/register": self._register_model,
            "/v1/broker/route": self._broker_route,
            "/v1/broker/failover": self._broker_failover,
            "/v1/broker/drain": self._broker_drain,
            "/v1/node/join": self._node_join,
        }
        handler = routes.get(path)
        if handler:
            try:
                handler()
            except json.JSONDecodeError:
                # Malformed request body — a client error, not a server crash.
                self._json_response({"error": "invalid JSON in request body"}, 400)
        else:
            self._json_response({"error": "not found", "path": path}, 404)

    def do_OPTIONS(self) -> None:
        """Do options."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET handlers ────────────────────────────────────

    def _health(self) -> dict[str, Any]:
        """Return health status."""
        report = self._get_report()
        self._json_response({
            "status": "ok",
            "initialized": self.store.is_initialized(),
            "profile": report.profile,
            "container_runtime": report.container_runtime,
            "uptime_seconds": time.time() - getattr(self.server, '_start_time', time.time()),
        })

    def _node_status(self) -> dict[str, Any]:
        """Return current node status as a dict."""
        node = self.store.load_node()
        report = self._get_report()
        self._json_response({
            "node": asdict(node),
            "system": asdict(report.system),
            "gpus": [asdict(g) for g in report.gpus],
            "npus": [asdict(n) for n in report.npus],
            "issues": report.issues,
        })

    def _runtime_info(self) -> dict[str, Any]:
        """Return runtime engine information dict."""
        report = self._get_report()
        self._json_response({
            "profile": report.profile,
            "container_runtime": report.container_runtime,
            "ollama": report.ollama_available,
            "gpus": [asdict(g) for g in report.gpus],
            "npus": [asdict(n) for n in report.npus],
            "recommendations": report.recommendations,
        })

    def _list_stacks(self) -> list[Any]:
        """Return list of running stack names."""
        stacks = self.store.load_stacks()
        self._json_response({"stacks": [asdict(s) for s in stacks]})

    def _list_services(self) -> list[Any]:
        """Return list of running service names."""
        services = list_running()
        self._json_response({"services": services})

    def _list_models(self) -> list[Any]:
        """Return list of loaded model names."""
        models = self.store.list_models()
        self._json_response({"models": models})

    def _list_recipes(self) -> None:
        """Return list of available recipe names."""
        names = list_recipes()
        recipes = []
        for n in names:
            m = get_recipe(n)
            if m:
                recipes.append({
                    "name": n,
                    "services": len(m.services),
                    "gpu_required": any(s.gpu_required for s in m.services),
                })
        self._json_response({"recipes": recipes})

    def _slo_status(self) -> None:
        """Return current SLO status report."""
        psi = read_psi()
        # In real impl, scrape vLLM /metrics — for now return PSI + placeholder
        metrics = InferenceMetrics(timestamp=time.time())
        target = SLOTarget()
        # Populate live goodput from recorded request spans, if available.
        try:
            from aictl.metrics.slo import goodput_from_spans
            spans_path = str(self.store.dir / "genai_spans.jsonl")
            gp = goodput_from_spans(spans_path, target)
            if gp.total_requests > 0:
                metrics.goodput_ratio = gp.goodput_ratio
                metrics.active_requests = gp.total_requests
        except Exception:
            pass  # best-effort; SLO status still returns PSI + placeholder
        verdict = check_slo(metrics, psi, target)
        self._json_response({
            "slo": {
                "compliant": verdict.compliant,
                "violations": verdict.violations,
                "action": verdict.action,
                "goodput_ratio": metrics.goodput_ratio,
            },
            "pressure": asdict(psi),
            "target": asdict(target),
        })

    def _psi_status(self) -> None:
        """Return PSI (Pressure Stall Information) status."""
        psi = read_psi()
        self._json_response(asdict(psi))

    def _upgrade_plan(self) -> None:
        """Upgrade to the latest version."""
        node = self.store.load_node()
        stacks = self.store.load_stacks()
        self._json_response({
            "current_version": node.version,
            "active_stacks": len(stacks),
            "steps": [
                "snapshot_state", "drain_workloads", "stage_update",
                "apply_update", "verify_health", "restore_workloads",
            ],
            "rollback": "bootc rollback",
        })

    # ── POST handlers ───────────────────────────────────

    def _apply_stack(self) -> None:
        """Apply a stack deployment."""
        body = self._read_body()
        file_path = body.get("file", "")
        if not file_path:
            self._json_response({"error": "missing 'file' field"}, 400)
            return
        try:
            manifest = parse_file(file_path)
        except StackParseError as e:
            self._json_response({"error": str(e)}, 400)
            return
        results = apply_stack(manifest)
        entry = StackEntry(
            name=manifest.name, file=file_path, applied_at=time.time(),
            status="running",
            services=[{"name": r.name, "status": r.status, "endpoint": r.endpoint} for r in results],
        )
        self.store.upsert_stack(entry)
        self._json_response({
            "stack": manifest.name,
            "services": [r.__dict__ for r in results],
        }, 201)

    def _down_stack(self) -> None:
        """Bring down a running stack."""
        body = self._read_body()
        name = body.get("name", "")
        if not name:
            self._json_response({"error": "missing 'name' field"}, 400)
            return
        stopped = stop_stack(name)
        self.store.remove_stack(name)
        self._json_response({"stopped": stopped})

    def _run_recipe(self) -> None:
        """Execute this subcommand and return an integer exit code."""
        body = self._read_body()
        name = body.get("name", "")
        manifest = get_recipe(name)
        if not manifest:
            self._json_response({"error": f"unknown recipe: {name}"}, 404)
            return
        results = apply_stack(manifest)
        entry = StackEntry(
            name=manifest.name, file=manifest.source_file, applied_at=time.time(),
            status="running",
            services=[{"name": r.name, "status": r.status, "endpoint": r.endpoint} for r in results],
        )
        self.store.upsert_stack(entry)
        self._json_response({
            "recipe": name,
            "services": [r.__dict__ for r in results],
        }, 201)

    def _register_model(self) -> None:
        """Register a model with the runtime broker."""
        body = self._read_body()
        import uuid
        mid = uuid.uuid4().hex[:8]
        self.store.register_model(
            model_id=mid,
            name=body.get("name", ""),
            digest=body.get("digest", ""),
            fmt=body.get("format", "gguf"),
            signed=body.get("signed", False),
        )
        self._json_response({"id": mid, "name": body.get("name", "")}, 201)

    # ── Broker handlers ─────────────────────────────────

    def _broker_engines(self) -> None:
        """Return status of all inference engines."""
        from aictl.runtime.adapters import discover_engines
        from aictl.core.config import load_config
        config = load_config(self.store.dir)
        healths = discover_engines(config.engines.to_dict())
        self._json_response({
            "engines": [
                {"engine": h.engine, "endpoint": h.endpoint, "reachable": h.reachable,
                 "status": h.status, "models": h.models, "version": h.version,
                 "latency_ms": round(h.latency_ms, 1), "error": h.error}
                for h in healths
            ]
        })

    def _broker_route(self) -> None:
        """Route an inference request to the optimal engine."""
        from aictl.runtime.router import BrokerRouter, RouteRequest
        from aictl.core.config import load_config
        body = self._read_body()
        config = load_config(self.store.dir)

        router = BrokerRouter(endpoints=config.engines.to_dict())
        req = RouteRequest(
            model=body.get("model", ""),
            objective=body.get("objective", "balanced"),
            tenant=body.get("tenant", ""),
            latency_slo_ms=body.get("latency_slo_ms", 0),
        )
        decision = router.route(req)
        self._json_response({
            "selected_engine": decision.selected_engine,
            "endpoint": decision.endpoint,
            "score": decision.score,
            "fallback_used": decision.fallback_used,
            "reason_codes": decision.reason_codes,
            "latency_ms": round(decision.latency_ms, 1),
        })

    def _broker_failover(self) -> None:
        """Trigger failover to a backup engine."""
        self._read_body()
        # Simplified failover: try engines in fallback order
        from aictl.runtime.adapters import discover_engines
        from aictl.core.config import load_config
        config = load_config(self.store.dir)
        healths = discover_engines(config.engines.to_dict())
        for h in healths:
            if h.reachable and h.status in ("READY", "DEGRADED"):
                self._json_response({
                    "fallback_target": h.engine,
                    "endpoint": h.endpoint,
                    "degraded_mode": h.status == "DEGRADED",
                })
                return
        self._json_response({"fallback_target": "", "endpoint": "", "degraded_mode": True})

    def _broker_drain(self) -> None:
        """Drain traffic from an engine before shutdown."""
        body = self._read_body()
        target = body.get("target", "")
        # Placeholder: in real impl, send drain signal to engine
        self._json_response({
            "target": target,
            "drained_sessions": 0,
            "forced_evictions": 0,
            "status": "acknowledged",
        })

    def _broker_governor(self) -> None:
        # Use the real governor instance if running as daemon
        """Return SLO governor status."""
        gov = getattr(self.__class__, '_governor', None)
        if gov:
            self._json_response(gov.get_status())
            return

        # Fallback: one-shot tick
        from aictl.runtime.router import BrokerRouter, SLOGovernor
        from aictl.core.config import load_config
        from aictl.metrics.slo import SLOTarget
        config = load_config(self.store.dir)
        slo_cfg = config.slo
        target = SLOTarget(
            ttft_p95_ms=slo_cfg.ttft_p95_ms,
            itl_p95_ms=slo_cfg.itl_p95_ms,
            tokens_per_sec_min=slo_cfg.tokens_per_sec_min,
            error_rate_max=slo_cfg.error_rate_max,
            queue_depth_max=slo_cfg.queue_depth_max,
            kv_cache_max=slo_cfg.kv_cache_max,
            psi_memory_some_max=slo_cfg.psi_memory_some_max,
        )
        router = BrokerRouter(endpoints=config.engines.to_dict(), slo_target=target)
        governor = SLOGovernor(router, target)
        action = governor.tick()
        self._json_response({
            "action": action.action,
            "engine": action.engine,
            "reason": action.reason,
            "timestamp": action.timestamp,
        })

    # ── Cluster handlers ────────────────────────────────

    def _cluster_status(self) -> None:
        """Return cluster node status."""
        from aictl.runtime.nodes import NodeManager
        mgr = NodeManager(self.store)
        cs = mgr.load_cluster()
        from dataclasses import asdict
        self._json_response(asdict(cs))

    def _node_join(self) -> None:
        """Handle a node join request."""
        from aictl.runtime.nodes import NodeManager
        body = self._read_body()
        mgr = NodeManager(self.store)
        result = mgr.accept_join(body)
        status = 200 if result.get("accepted") else 403
        self._json_response(result, status)

    # ── Prometheus + Events ─────────────────────────────

    def _prometheus_metrics(self) -> None:
        """Return Prometheus-format metrics for the mock engine."""
        from aictl.metrics.prometheus import generate_metrics_text
        text = generate_metrics_text(self.store)
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _events(self) -> None:
        """Stream recent events as SSE."""
        from aictl.core.events import get_bus
        from dataclasses import asdict
        bus = get_bus()
        events = bus.recent(20)
        self._json_response({
            "events": [asdict(e) for e in events],
            "total": len(bus._history),
        })

    def _fabric_info(self) -> None:
        """Return memory fabric topology."""
        from aictl.runtime.fabric import detect_memory_fabric, generate_placement_policy
        from dataclasses import asdict
        report = detect_memory_fabric()
        node = self.store.load_node()
        vram = node.vram_total_mb // 1024
        policy = generate_placement_policy(report, vram_gb=vram)
        self._json_response({
            "fabric": asdict(report),
            "placement_policy": asdict(policy),
        })

    def _context_list(self) -> None:
        """Return active inference contexts."""
        from aictl.runtime.continuity import ContextContinuityEngine
        from dataclasses import asdict
        engine = ContextContinuityEngine()
        snaps = engine.list_snapshots()
        self._json_response({
            "snapshots": [asdict(s) for s in snaps],
            "total": len(snaps),
        })

    def _recommend(self) -> None:
        """Return model recommendations for current hardware."""
        from aictl.runtime.recommend import recommend
        node = self.store.load_node()
        recs = recommend(vram_mb=node.vram_total_mb, ram_mb=node.ram_total_mb, max_results=5)
        self._json_response({
            "recommendations": [
                {"name": r.name, "runtime": r.runtime, "vram_mb": r.vram_required_mb,
                 "use_case": r.use_case, "notes": r.notes}
                for r in recs
            ],
        })

    def _apikeys_list(self) -> None:
        """Return list of registered API keys."""
        from aictl.core.apikeys import KeyManager
        mgr = KeyManager(self.store.dir)
        self._json_response({"keys": mgr.list_keys()})

    def _audit_recent(self) -> None:
        """Return recent audit log entries."""
        from aictl.core.audit import AuditLog
        from dataclasses import asdict
        log = AuditLog(self.store.dir)
        entries = log.read(n=20)
        self._json_response({"entries": [asdict(e) for e in entries]})

    def _dynamo_status(self) -> None:
        """Return NVIDIA Dynamo status."""
        from aictl.runtime.dynamo import detect_dynamo, generate_kvbm_config
        from dataclasses import asdict
        status = detect_dynamo()
        kvbm = asdict(generate_kvbm_config())
        self._json_response({"dynamo": status, "kvbm": kvbm})

    def _metering_status(self) -> None:
        """Return token metering status."""
        from aictl.core.metering import TokenMeter
        from dataclasses import asdict
        meter = TokenMeter(self.store.dir)
        buckets = meter.list_usage()
        self._json_response({
            "entities": [asdict(b) for b in buckets],
            "total_entities": len(buckets),
            "total_tokens": sum(b.total_tokens for b in buckets),
        })


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server for handling concurrent API requests."""
    daemon_threads = True


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          state_dir: Path | None = None) -> None:
    """Start the aiosd daemon with background SLO Governor."""
    store = StateStore(state_dir)
    AIOSHandler.store = store

    # Start SLO Governor
    from aictl.daemon.governor import GovernorDaemon
    governor = GovernorDaemon(store, interval_s=15.0)
    AIOSHandler._governor = governor  # type: ignore
    governor.start()

    server = ThreadedHTTPServer((host, port), AIOSHandler)
    server._start_time = time.time()  # type: ignore

    def shutdown(sig: Any, frame: Any) -> None:
        """Shutdown."""
        print("\naiosd shutting down...")
        governor.stop()
        threading.Thread(target=server.shutdown).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"aiosd listening on http://{host}:{port}")
    print(f"State dir: {store.dir}")
    print("SLO Governor: active (15s interval)")
    server.serve_forever()


if __name__ == "__main__":
    serve()

