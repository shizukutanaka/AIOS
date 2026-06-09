"""SLO Governor daemon: background thread for continuous SLO monitoring.

Runs inside aiosd, ticking every N seconds. When SLO violations are
detected, it logs the action and can optionally execute corrective
measures (rebalance routing weights, drain overloaded engines, etc.).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from aictl.core.config import load_config
from aictl.core.state import StateStore
from aictl.metrics.slo import (
    SLOTarget, check_slo, read_psi,
)
from aictl.runtime.adapters import get_adapter
from aictl.runtime.router import GovernorAction

logger = logging.getLogger("aios.governor")


@dataclass
class GovernorState:
    running: bool = False
    tick_count: int = 0
    last_tick: float = 0.0
    last_action: GovernorAction = field(default_factory=GovernorAction)
    history: list[GovernorAction] = field(default_factory=list)
    consecutive_violations: int = 0


class GovernorDaemon:
    """Background SLO monitoring thread."""

    def __init__(self, store: StateStore, interval_s: float = 15.0,
                 on_action: Callable[[GovernorAction], None] | None = None):
        """Initialize SLO governor with store and targets."""
        self.store = store
        self.interval = interval_s
        self.on_action = on_action
        self.state = GovernorState()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._max_history = 200

    def start(self) -> None:
        """Start."""
        if self.state.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="slo-governor")
        self._thread.start()
        self.state.running = True
        logger.info("SLO Governor started (interval=%.1fs)", self.interval)

    def stop(self) -> None:
        """Stop."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.state.running = False
        logger.info("SLO Governor stopped")

    def _loop(self) -> None:
        """Run the governor main loop."""
        while not self._stop.is_set():
            try:
                action = self._tick()
                self.state.tick_count += 1
                self.state.last_tick = time.time()
                self.state.last_action = action

                self.state.history.append(action)
                if len(self.state.history) > self._max_history:
                    self.state.history = self.state.history[-self._max_history:]

                if action.action != "none":
                    self.state.consecutive_violations += 1
                    logger.warning(
                        "SLO violation #%d: %s on %s — %s",
                        self.state.consecutive_violations,
                        action.action, action.engine, action.reason,
                    )
                    if self.on_action:
                        self.on_action(action)
                else:
                    self.state.consecutive_violations = 0

                # Export metrics to OTel (best effort)
                self._export_otel()

            except Exception as e:
                logger.error("Governor tick error: %s", e)

            self._stop.wait(self.interval)

    def _tick(self) -> GovernorAction:
        """Execute one SLO evaluation tick."""
        config = load_config(self.store.dir)
        target = SLOTarget(
            ttft_p95_ms=config.slo.ttft_p95_ms,
            itl_p95_ms=config.slo.itl_p95_ms,
            tokens_per_sec_min=config.slo.tokens_per_sec_min,
            error_rate_max=config.slo.error_rate_max,
            queue_depth_max=config.slo.queue_depth_max,
            kv_cache_max=config.slo.kv_cache_max,
            psi_memory_some_max=config.slo.psi_memory_some_max,
        )

        psi = read_psi()
        action = GovernorAction(timestamp=time.time())

        # Check each engine
        endpoints = config.engines.to_dict()
        worst_violations: list[str] = []
        worst_engine = ""
        worst_action = "none"

        for engine_name, endpoint in endpoints.items():
            adapter = get_adapter(engine_name, endpoint)
            if not adapter:
                continue

            try:
                health = adapter.health()
                if not health.reachable:
                    continue
                metrics = adapter.scrape_metrics()
            except Exception:
                continue

            verdict = check_slo(metrics, psi, target)
            if not verdict.compliant:
                if len(verdict.violations) > len(worst_violations):
                    worst_violations = verdict.violations
                    worst_engine = engine_name
                    worst_action = verdict.action

        if worst_violations:
            action.action = worst_action
            action.engine = worst_engine
            action.reason = "; ".join(worst_violations)
            action.details = {
                "violations": worst_violations,
                "psi_memory": psi.memory_some_avg10,
                "consecutive": self.state.consecutive_violations,
            }

            # Escalate if consecutive violations exceed threshold.
            # Check the more severe threshold first so violations>=10 always
            # reach failover regardless of the current action type.
            if self.state.consecutive_violations >= 10:
                action.action = "failover"
                action.reason += " [escalated: 10+ consecutive violations]"
            elif self.state.consecutive_violations >= 5 and action.action == "scale_batch":
                action.action = "drain"
                action.reason += " [escalated: 5+ consecutive violations]"

        return action

    def get_status(self) -> dict[str, Any]:
        """Get status."""
        return {
            "running": self.state.running,
            "tick_count": self.state.tick_count,
            "last_tick": self.state.last_tick,
            "consecutive_violations": self.state.consecutive_violations,
            "last_action": asdict(self.state.last_action) if self.state.last_action else None,
            "recent_violations": [
                asdict(a) for a in self.state.history if a.action != "none"
            ][-10:],
        }

    def _export_otel(self) -> None:
        """Best-effort export of latest metrics to OTel Collector."""
        try:
            config = load_config(self.store.dir)
            node = self.store.load_node()
            endpoints = config.engines.to_dict()
            psi = read_psi()

            for engine_name, endpoint in endpoints.items():
                adapter = get_adapter(engine_name, endpoint)
                if not adapter:
                    continue
                health = adapter.health()
                if not health.reachable:
                    continue

                metrics = adapter.scrape_metrics()
                from aictl.metrics.otel import export_metrics
                export_metrics(
                    metrics, psi,
                    node_id=node.node_id,
                    profile=node.profile,
                )
        except Exception:
            pass  # Best effort — never crash the governor
