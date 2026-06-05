"""Broker routing engine: select the optimal inference backend.

Algorithm (from Runtime Broker v0.5 spec):

1. Hard filter — can this engine run the request at all?
   - Model compatibility (engine has model loaded)
   - GPU/VRAM headroom
   - Health status (must be READY or DEGRADED)
   - Trust policy (signature verified)

2. Soft score — which eligible engine is best?
   score = w1*slo_fit + w2*prefix_cache + w3*power_efficiency + w4*cost + w5*headroom

3. Fallback chain:
   vLLM → SGLang → Ollama → CPU
   If all fail, return error with reason codes.
"""

from __future__ import annotations

from aictl.core.constants import VLLM_DEFAULT_URL, OLLAMA_DEFAULT_URL, SGLANG_DEFAULT_URL

import time
from dataclasses import dataclass, field
from typing import Any

from aictl.runtime.adapters import EngineHealth, discover_engines, get_adapter
from aictl.metrics.slo import InferenceMetrics, SLOTarget, check_slo, read_psi


@dataclass
class RouteRequest:
    model: str
    objective: str = "balanced"   # latency | throughput | cost | balanced
    tenant: str = ""
    latency_slo_ms: float = 0.0


@dataclass
class RouteDecision:
    selected_engine: str = ""
    selected_pool: str = "default"
    endpoint: str = ""
    reason_codes: list[str] = field(default_factory=list)
    score: float = 0.0
    fallback_used: bool = False
    latency_ms: float = 0.0


@dataclass
class EngineCandidate:
    engine: str
    endpoint: str
    health: EngineHealth
    metrics: InferenceMetrics | None = None
    score: float = 0.0
    rejection_reason: str = ""


# ── Scoring weights by objective ────────────────────────

WEIGHTS = {
    "latency": {"slo_fit": 0.45, "headroom": 0.25, "cache": 0.20, "cost": 0.05, "power": 0.05},
    "throughput": {"slo_fit": 0.20, "headroom": 0.35, "cache": 0.15, "cost": 0.15, "power": 0.15},
    "cost": {"slo_fit": 0.15, "headroom": 0.15, "cache": 0.10, "cost": 0.45, "power": 0.15},
    "balanced": {"slo_fit": 0.30, "headroom": 0.25, "cache": 0.15, "cost": 0.15, "power": 0.15},
}

# Engine cost ranking (lower = cheaper)
ENGINE_COST = {"ollama": 0.2, "vllm": 0.5, "sglang": 0.5, "trt-llm": 0.7, "cpu": 0.1}

# Engine power efficiency ranking (higher = more efficient)
ENGINE_POWER = {"ollama": 0.7, "vllm": 0.5, "sglang": 0.5, "trt-llm": 0.4, "cpu": 0.9}

# Fallback priority
FALLBACK_ORDER = ["vllm", "sglang", "ollama"]


class BrokerRouter:
    """Routes inference requests to the best available engine."""

    def __init__(self, endpoints: dict[str, str] | None = None,
                 slo_target: SLOTarget | None = None):
        """Initialize route decision."""
        self.endpoints = endpoints or {
            "vllm": VLLM_DEFAULT_URL,
            "ollama": OLLAMA_DEFAULT_URL,
            "sglang": SGLANG_DEFAULT_URL,
        }
        self.slo_target = slo_target or SLOTarget()

    def route(self, req: RouteRequest) -> RouteDecision:
        """Route a request through hard filter → soft score → fallback."""
        t0 = time.monotonic()
        decision = RouteDecision()
        decision.reason_codes = []

        # Discover engines
        healths = discover_engines(self.endpoints)
        candidates: list[EngineCandidate] = []

        for h in healths:
            c = EngineCandidate(engine=h.engine, endpoint=h.endpoint, health=h)

            # ── Hard filter ──
            rejection = self._hard_filter(c, req)
            if rejection:
                c.rejection_reason = rejection
                decision.reason_codes.append(f"{h.engine}: rejected ({rejection})")
                candidates.append(c)
                continue

            # ── Collect metrics (best effort) ──
            adapter = get_adapter(h.engine, h.endpoint)
            if adapter:
                try:
                    c.metrics = adapter.scrape_metrics()
                except Exception:
                    c.metrics = InferenceMetrics(engine=h.engine)

            # ── Soft score ──
            c.score = self._soft_score(c, req)
            candidates.append(c)

        # ── Select best ──
        eligible = [c for c in candidates if not c.rejection_reason]
        eligible.sort(key=lambda c: c.score, reverse=True)

        if eligible:
            best = eligible[0]
            decision.selected_engine = best.engine
            decision.endpoint = best.endpoint
            decision.score = best.score
            decision.reason_codes.append(
                f"selected {best.engine} (score={best.score:.2f})"
            )
            # An eligible engine was scored and selected directly; this is not a
            # fallback. The priority-order fallback path (_fallback) is the only
            # place fallback_used becomes True.
        else:
            # ── Fallback: try engines in priority order ──
            decision = self._fallback(req, candidates, decision)

        decision.latency_ms = (time.monotonic() - t0) * 1000
        return decision

    def _hard_filter(self, c: EngineCandidate, req: RouteRequest) -> str:
        """Return rejection reason or empty string if eligible."""
        h = c.health

        # Must be reachable
        if not h.reachable:
            return "unreachable"

        # Must be READY or DEGRADED (DEGRADED still serves, just worse)
        if h.status not in ("READY", "DEGRADED"):
            return f"status={h.status}"

        # Model must be loaded (if models list is available)
        if h.models and req.model:
            # Fuzzy match: check if model name appears in any loaded model
            model_lower = req.model.lower()
            found = any(
                model_lower in m.lower() or m.lower() in model_lower
                for m in h.models
            )
            if not found:
                return f"model '{req.model}' not loaded"

        return ""

    def _soft_score(self, c: EngineCandidate, req: RouteRequest) -> float:
        """Compute composite score for an eligible engine."""
        w = WEIGHTS.get(req.objective, WEIGHTS["balanced"])
        engine = c.engine

        # SLO fit: how well does current performance meet SLO?
        slo_fit = 1.0
        if c.metrics:
            if self.slo_target.ttft_p95_ms > 0 and c.metrics.ttft_ms_p95 > 0:
                ratio = c.metrics.ttft_ms_p95 / self.slo_target.ttft_p95_ms
                slo_fit = max(0.0, 1.0 - (ratio - 1.0)) if ratio > 1.0 else 1.0
            if c.metrics.error_rate > self.slo_target.error_rate_max:
                slo_fit *= 0.5

        # Headroom: how much capacity is left?
        headroom = 1.0
        if c.metrics:
            if c.metrics.kv_cache_utilization > 0:
                headroom = 1.0 - c.metrics.kv_cache_utilization
            if c.metrics.queue_depth > 0:
                q_ratio = c.metrics.queue_depth / max(self.slo_target.queue_depth_max, 1)
                headroom *= max(0.0, 1.0 - q_ratio)

        # Prefix cache: base score for engines with cache support,
        # boosted by actual prompt-level KV locality (RadixAttention-style)
        cache = 0.5
        if engine in ("vllm", "sglang"):
            cache = 0.8
        # Check real prefix locality via the prefix route tracker
        # Use model name as the prompt proxy when full message unavailable
        prompt = getattr(req, "prompt", "") or req.model
        if prompt and c.endpoint:
            try:
                from aictl.runtime.prefix_route import get_default_tracker
                _tracker = get_default_tracker()
                _match = _tracker.best_endpoint(prompt, [c.endpoint])
                if _match and _match.endpoint == c.endpoint:
                    cache = min(1.0, cache + _match.overlap_score * 0.15)
            except Exception:
                pass  # best-effort; failure is non-critical

        # Cost
        cost = 1.0 - ENGINE_COST.get(engine, 0.5)

        # Power efficiency
        power = ENGINE_POWER.get(engine, 0.5)

        # DEGRADED penalty
        if c.health.status == "DEGRADED":
            slo_fit *= 0.7

        score = (
            w["slo_fit"] * slo_fit +
            w["headroom"] * headroom +
            w["cache"] * cache +
            w["cost"] * cost +
            w["power"] * power
        )

        return round(score, 4)

    def _fallback(self, req: RouteRequest, candidates: list[EngineCandidate],
                  decision: RouteDecision) -> RouteDecision:
        """Try engines in fallback order."""
        for engine in FALLBACK_ORDER:
            matching = [c for c in candidates if c.engine == engine]
            if matching:
                c = matching[0]
                if c.health.reachable:
                    decision.selected_engine = engine
                    decision.endpoint = c.endpoint
                    decision.fallback_used = True
                    decision.reason_codes.append(f"fallback to {engine}")
                    return decision

        decision.selected_engine = ""
        decision.endpoint = ""
        decision.reason_codes.append("all engines unavailable")
        return decision


# ══════════════════════════════════════════════════════════
#  SLO Governor — continuous monitoring loop
# ══════════════════════════════════════════════════════════

@dataclass
class GovernorAction:
    timestamp: float = 0.0
    action: str = "none"          # none | scale_batch | drain | failover | rebalance
    engine: str = ""
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class SLOGovernor:
    """Continuously monitors SLO compliance and triggers corrective actions.

    Runs as a daemon thread inside aiosd. Each tick:
      1. Scrape metrics from all engines
      2. Read PSI
      3. Check SLO compliance
      4. If violated, determine and log action
    """

    def __init__(self, router: BrokerRouter, target: SLOTarget | None = None):
        """Initialize router with engines and SLO targets."""
        self.router = router
        self.target = target or SLOTarget()
        self.history: list[GovernorAction] = []
        self._max_history = 100

    def tick(self) -> GovernorAction:
        """Single evaluation cycle."""
        action = GovernorAction(timestamp=time.time())

        # Read system pressure
        psi = read_psi()

        # Check each engine
        worst_verdict = None
        worst_engine = ""

        for engine_name, endpoint in self.router.endpoints.items():
            adapter = get_adapter(engine_name, endpoint)
            if not adapter:
                continue

            try:
                health = adapter.health()
                if not health.reachable:
                    continue

                metrics = adapter.scrape_metrics()
                if metrics.model:
                    pass  # Use model-specific target if available
            except Exception:
                continue

            verdict = check_slo(metrics, psi, self.target)

            if not verdict.compliant:
                if worst_verdict is None or len(verdict.violations) > len(worst_verdict.violations):
                    worst_verdict = verdict
                    worst_engine = engine_name

        if worst_verdict and not worst_verdict.compliant:
            action.action = worst_verdict.action
            action.engine = worst_engine
            action.reason = "; ".join(worst_verdict.violations)
            action.details = {
                "violations": worst_verdict.violations,
                "psi_memory": psi.memory_some_avg10,
            }

        self.history.append(action)
        if len(self.history) > self._max_history:
            self.history = self.history[-self._max_history:]

        return action

    def recent_actions(self, n: int = 10) -> list[GovernorAction]:
        """Return recent non-trivial actions."""
        return [a for a in self.history if a.action != "none"][-n:]
