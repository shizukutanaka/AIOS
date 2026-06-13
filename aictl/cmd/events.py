"""aictl events — query and stream the AIOS event bus."""

from __future__ import annotations

from typing import Any

import argparse
import time

from aictl.core.output import ok, print_json
from aictl.core.events import get_bus

_KNOWN_TYPES = [
    "stack.applied", "stack.stopped",
    "engine.ready", "engine.degraded", "engine.offline",
    "slo.violation", "slo.recovered",
    "node.joined", "node.left",
    "snapshot.created", "model.registered",
    "upgrade.started",
]


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("events", help="Query and stream the AIOS event bus")
    esub = p.add_subparsers(dest="events_cmd")

    ls = esub.add_parser("list", help="List recent events")
    ls.add_argument("--type", default="", dest="event_type", help="Filter by event type")
    ls.add_argument("--limit", "-n", type=int, default=20, help="Max events (default 20)")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=run_list)

    watch = esub.add_parser("watch", help="Stream new events as they arrive")
    watch.add_argument("--type", default="", dest="event_type")
    watch.add_argument("--json", action="store_true")
    watch.set_defaults(func=run_watch)

    clr = esub.add_parser("clear", help="Clear in-process event history")
    clr.set_defaults(func=run_clear)

    types_p = esub.add_parser("types", help="List known event types")
    types_p.add_argument("--json", action="store_true")
    types_p.set_defaults(func=run_types)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def _event_to_dict(e: Any) -> dict[str, Any]:
    return {
        "type": e.type,
        "source": e.source,
        "timestamp": e.timestamp,
        "time": time.strftime("%H:%M:%S", time.localtime(e.timestamp)),
        "data": e.data,
    }


def run_list(args: argparse.Namespace) -> int:
    """Show recent events from the in-process event bus."""
    limit = getattr(args, "limit", 20)
    event_type = getattr(args, "event_type", "")
    events = get_bus().recent(n=limit, event_type=event_type)

    dicts = [_event_to_dict(e) for e in events]

    if getattr(args, "json", False):
        print_json(dicts)
        return 0

    if not events:
        suffix = f" (type={event_type})" if event_type else ""
        print(f"No events recorded{suffix}.")
        return 0

    for d in dicts:
        print(f"  {d['time']}  {d['type']:<35}  {d['source']}")
    return 0


def run_watch(args: argparse.Namespace) -> int:
    """Stream events as they arrive in the in-process bus."""
    event_type = getattr(args, "event_type", "")
    use_json = getattr(args, "json", False)

    seen: set[int] = set()
    for e in get_bus().recent(n=500):
        seen.add(id(e))

    suffix = f" (type={event_type})" if event_type else ""
    print(f"Watching events{suffix}... (Ctrl-C to stop)")
    try:
        while True:
            for e in get_bus().recent(n=500):
                eid = id(e)
                if eid in seen:
                    continue
                seen.add(eid)
                if event_type and e.type != event_type:
                    continue
                d = _event_to_dict(e)
                if use_json:
                    print_json(d)
                else:
                    print(f"  {d['time']}  {d['type']:<35}  {d['source']}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    return 0


def run_clear(args: argparse.Namespace) -> int:
    """Clear the in-process event history."""
    get_bus().clear()
    ok("Event history cleared")
    return 0


def run_types(args: argparse.Namespace) -> int:
    """List the known event types."""
    if getattr(args, "json", False):
        print_json(_KNOWN_TYPES)
        return 0
    print("Known event types:")
    for t in _KNOWN_TYPES:
        print(f"  {t}")
    return 0
