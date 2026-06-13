"""aictl hooks — inspect and test integration hooks."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, print_json, print_table


# Known hooks: (name, description, sample args for test)
_HOOKS: list[dict[str, Any]] = [
    {
        "name": "on_stack_applied",
        "description": "Emits stack.applied event + audit entry when a stack is applied",
        "module": "aictl.core.hooks",
        "events": ["stack.applied"],
        "audit": ["stack.applied"],
    },
    {
        "name": "on_stack_stopped",
        "description": "Emits stack.stopped event + audit entry when a stack stops",
        "module": "aictl.core.hooks",
        "events": ["stack.stopped"],
        "audit": ["stack.stopped"],
    },
    {
        "name": "on_model_registered",
        "description": "Emits model.registered event + audit entry",
        "module": "aictl.core.hooks",
        "events": ["model.registered"],
        "audit": ["model.registered"],
    },
    {
        "name": "on_model_verified",
        "description": "Writes audit entry for model signature verification",
        "module": "aictl.core.hooks",
        "events": [],
        "audit": ["model.verified", "trust.violation"],
    },
    {
        "name": "on_snapshot_created",
        "description": "Emits snapshot.created event + audit entry",
        "module": "aictl.core.hooks",
        "events": ["snapshot.created"],
        "audit": ["snapshot.created"],
    },
    {
        "name": "on_engine_health_changed",
        "description": "Emits engine.ready or engine.offline event on health change",
        "module": "aictl.core.hooks",
        "events": ["engine.ready", "engine.offline"],
        "audit": [],
    },
    {
        "name": "on_slo_violation",
        "description": "Emits slo.violation event + audit warning",
        "module": "aictl.core.hooks",
        "events": ["slo.violation"],
        "audit": ["slo.violation"],
    },
    {
        "name": "on_proxy_request",
        "description": "Writes audit entry for each proxied inference request",
        "module": "aictl.core.hooks",
        "events": [],
        "audit": ["proxy.request"],
    },
    {
        "name": "on_node_joined",
        "description": "Emits node.joined event + audit entry when a node joins",
        "module": "aictl.core.hooks",
        "events": ["node.joined"],
        "audit": ["node.joined"],
    },
    {
        "name": "on_config_changed",
        "description": "Writes audit entry when configuration is modified",
        "module": "aictl.core.hooks",
        "events": [],
        "audit": ["config.changed"],
    },
]

_HOOK_MAP = {h["name"]: h for h in _HOOKS}


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("hooks", help="Inspect and test integration hooks")
    hsub = p.add_subparsers(dest="hooks_cmd")

    ls = hsub.add_parser("list", help="List all known hooks")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=run_list)

    test = hsub.add_parser("test", help="Dry-run a hook with sample data")
    test.add_argument("name", help="Hook name (e.g. on_stack_applied)")
    test.add_argument("--json", action="store_true")
    test.set_defaults(func=run_test)

    emit = hsub.add_parser("emit", help="Emit a test event to the event bus")
    emit.add_argument("event_type", help="Event type (e.g. stack.applied)")
    emit.add_argument("--source", default="test")
    emit.set_defaults(func=run_emit)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_list(args: argparse.Namespace) -> int:
    """List all known hooks with their events and audit actions."""
    rows = [
        {
            "name": h["name"],
            "events": ", ".join(h["events"]) if h["events"] else "(none)",
            "audit": ", ".join(h["audit"]) if h["audit"] else "(none)",
        }
        for h in _HOOKS
    ]

    if getattr(args, "json", False):
        print_json(_HOOKS)
        return 0

    print_table(rows, ["name", "events", "audit"])
    print(f"\n  {len(_HOOKS)} hooks registered in aictl.core.hooks")
    return 0


def run_test(args: argparse.Namespace) -> int:
    """Dry-run a hook: call it with sample data and capture events/audit."""
    from aictl.core.events import get_bus
    import aictl.core.hooks as hooks_module

    hook_name = args.name
    if hook_name not in _HOOK_MAP:
        err(f"Unknown hook: {hook_name}")
        err(f"Available: {', '.join(_HOOK_MAP)}")
        return 1

    fn = getattr(hooks_module, hook_name, None)
    if fn is None:
        err(f"Hook {hook_name!r} is not callable")
        return 1

    meta = _HOOK_MAP[hook_name]
    # Record event bus count before
    bus = get_bus()
    before_count = len(bus.recent(n=500))

    # Invoke with sample data
    try:
        if hook_name == "on_stack_applied":
            fn("test-stack", file="test.yaml", mode="direct", services=2)
        elif hook_name == "on_stack_stopped":
            fn("test-stack")
        elif hook_name == "on_model_registered":
            fn("test-model", digest="sha256:abc", runtime="ollama")
        elif hook_name == "on_model_verified":
            fn("test-model", method="cosign", valid=True)
        elif hook_name == "on_snapshot_created":
            fn("snap_001", label="test")
        elif hook_name == "on_engine_health_changed":
            fn("vllm", status="READY", endpoint="http://localhost:8000")
        elif hook_name == "on_slo_violation":
            fn("vllm", metric="ttft_p95_ms", value=900.0, threshold=500.0, action="alert")
        elif hook_name == "on_proxy_request":
            fn(key_name="test-key", model="llama3", engine="vllm", tokens=128)
        elif hook_name == "on_node_joined":
            fn("node-001", hostname="worker1", address="192.168.1.2")
        elif hook_name == "on_config_changed":
            fn("log_level", old_value="info", new_value="debug")
        else:
            err(f"No sample data available for {hook_name}")
            return 1
    except Exception as e:
        err(f"Hook raised exception: {e}")
        return 1

    after_count = len(bus.recent(n=500))
    new_events = after_count - before_count

    result = {
        "hook": hook_name,
        "status": "ok",
        "events_emitted": new_events,
        "expected_events": meta["events"],
        "expected_audit": meta["audit"],
    }

    if getattr(args, "json", False):
        print_json(result)
        return 0

    ok(f"Hook {hook_name!r} executed successfully")
    print(f"  events emitted : {new_events}")
    print(f"  expected events: {', '.join(meta['events']) if meta['events'] else '(none)'}")
    print(f"  expected audit : {', '.join(meta['audit']) if meta['audit'] else '(none)'}")
    return 0


def run_emit(args: argparse.Namespace) -> int:
    """Emit a test event directly to the in-process event bus."""
    from aictl.core.events import emit as _emit, get_bus

    event_type = args.event_type
    source = getattr(args, "source", "test")

    bus = get_bus()
    before = len(bus.recent(n=500))
    _emit(event_type, source=source, test=True)
    after = len(bus.recent(n=500))

    ok(f"Emitted {event_type!r} from source={source!r}")
    if after > before:
        print(f"  Event bus now has {after} entries")
    return 0
