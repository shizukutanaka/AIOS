"""Scheduler daemon: background thread that fires due batch jobs / warmup
schedules — the missing piece that made `aictl batch add --schedule` and
`aictl warmup schedule` look automatic while being entirely manual
(docs/FEATURE_GAP_AUDIT.md P3/P4/M3).

Mirrors aictl.daemon.governor.GovernorDaemon's start/stop/background-thread
shape. Ticks every `interval_s` (default 60s — cron schedules are
minute-grained, so checking more often than once a minute buys nothing) and
calls aictl.core.scheduler.run_due_all, which is itself idempotent within a
matching minute (see core/scheduler.py's cron_is_due).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from aictl.core.state import StateStore

logger = logging.getLogger("aios.scheduler")


@dataclass
class SchedulerState:
    running: bool = False
    tick_count: int = 0
    last_tick: float = 0.0
    last_result: dict[str, Any] = field(default_factory=dict)


class SchedulerDaemon:
    """Background thread that runs due batch jobs / warmup schedules."""

    def __init__(self, store: StateStore, interval_s: float = 60.0):
        self.store = store
        self.interval = interval_s
        self.state = SchedulerState()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        """Start the background thread (no-op if already running)."""
        if self.state.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="aios-scheduler")
        self._thread.start()
        self.state.running = True
        logger.info("Scheduler started (interval=%.1fs)", self.interval)

    def stop(self) -> None:
        """Stop the background thread."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.state.running = False
        logger.info("Scheduler stopped")

    def _loop(self) -> None:
        """Tick forever until stopped, sleeping `interval` between ticks."""
        while not self._stop.is_set():
            try:
                result = self.tick()
                with self._state_lock:
                    self.state.tick_count += 1
                    self.state.last_tick = time.time()
                    self.state.last_result = result
                    n_jobs = len(result.get("batch_jobs", []))
                    if n_jobs or result.get("warmup"):
                        logger.info("Scheduler tick ran %d batch job(s), warmup=%s",
                                   n_jobs, bool(result.get("warmup")))
            except Exception as e:
                logger.error("Scheduler tick error: %s", e)
            self._stop.wait(self.interval)

    def tick(self) -> dict[str, Any]:
        """Run one tick synchronously (also used by tests / a manual trigger)."""
        from aictl.core.scheduler import run_due_all
        return run_due_all(self.store.dir)

    def get_status(self) -> dict[str, Any]:
        """Snapshot of scheduler state for introspection."""
        with self._state_lock:
            return {
                "running": self.state.running,
                "tick_count": self.state.tick_count,
                "last_tick": self.state.last_tick,
                "last_result": self.state.last_result,
                "interval_s": self.interval,
            }
