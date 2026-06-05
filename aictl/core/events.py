"""Event bus: lightweight internal pub/sub for state change notifications.

Events flow through aiosd to coordinate between subsystems:
  - Stack applied/stopped → update metrics, notify governor
  - Engine health changed → trigger routing update
  - SLO violation → log, notify, auto-remediate
  - Node joined/left → update cluster state
  - Snapshot created → audit log
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("aios.events")


@dataclass
class Event:
    type: str                    # stack.applied | engine.degraded | slo.violation | node.joined | ...
    source: str = ""             # module that emitted the event
    timestamp: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Set defaults for event."""
        if self.timestamp == 0:
            self.timestamp = time.time()


# Event types
STACK_APPLIED = "stack.applied"
STACK_STOPPED = "stack.stopped"
ENGINE_READY = "engine.ready"
ENGINE_DEGRADED = "engine.degraded"
ENGINE_OFFLINE = "engine.offline"
SLO_VIOLATION = "slo.violation"
SLO_RECOVERED = "slo.recovered"
NODE_JOINED = "node.joined"
NODE_LEFT = "node.left"
SNAPSHOT_CREATED = "snapshot.created"
MODEL_REGISTERED = "model.registered"
UPGRADE_STARTED = "upgrade.started"

Listener = Callable[[Event], None]


class EventBus:
    """Thread-safe pub/sub event bus."""

    def __init__(self, max_history: int = 500):
        """Initialize event bus."""
        self._listeners: dict[str, list[Listener]] = {}
        self._global_listeners: list[Listener] = []
        self._history: list[Event] = []
        self._max_history = max_history
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, listener: Listener) -> None:
        """Subscribe."""
        with self._lock:
            if event_type not in self._listeners:
                self._listeners[event_type] = []
            self._listeners[event_type].append(listener)

    def subscribe_all(self, listener: Listener) -> None:
        """Subscribe all."""
        with self._lock:
            self._global_listeners.append(listener)

    def publish(self, event: Event) -> None:
        """Publish."""
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

            listeners = list(self._global_listeners)
            if event.type in self._listeners:
                listeners.extend(self._listeners[event.type])

        for listener in listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error("Event listener error for %s: %s", event.type, e)

    def recent(self, n: int = 20, event_type: str = "") -> list[Event]:
        """Recent."""
        with self._lock:
            if event_type:
                filtered = [e for e in self._history if e.type == event_type]
                return filtered[-n:]
            return self._history[-n:]

    def clear(self) -> None:
        """Clear."""
        with self._lock:
            self._history.clear()


# Global singleton
_bus: EventBus | None = None


def get_bus() -> EventBus:
    """Get bus."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def emit(event_type: str, source: str = "", **data: Any) -> None:
    """Emit."""
    get_bus().publish(Event(type=event_type, source=source, data=data))
