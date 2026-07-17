"""aictl hooks — inspect, test, and subscribe to integration hooks."""

from __future__ import annotations

from typing import Any

import argparse
from pathlib import Path

from aictl.core.output import ok, err, print_json, print_table


# Known hooks: (name, description, sample args for test)
#
# "wired": whether a real production code path calls this hook function
# today (not just tests / `aictl hooks test`). "dispatches": whether firing
# this hook can run a subscribed webhook/script (see `aictl hooks add`).
# on_proxy_request deliberately never dispatches -- it fires on every single
# completions request, and a synchronous webhook/script call there would add
# latency to the hot inference path.
_HOOKS: list[dict[str, Any]] = [
    {
        "name": "on_stack_applied",
        "description": "Emits stack.applied event + audit entry when a stack is applied",
        "module": "aictl.core.hooks",
        "events": ["stack.applied"],
        "audit": ["stack.applied"],
        "wired": True,
        "dispatches": True,
    },
    {
        "name": "on_stack_stopped",
        "description": "Emits stack.stopped event + audit entry when a stack stops",
        "module": "aictl.core.hooks",
        "events": ["stack.stopped"],
        "audit": ["stack.stopped"],
        "wired": True,
        "dispatches": True,
    },
    {
        "name": "on_model_registered",
        "description": "Emits model.registered event + audit entry",
        "module": "aictl.core.hooks",
        "events": ["model.registered"],
        "audit": ["model.registered"],
        "wired": True,
        "dispatches": True,
    },
    {
        "name": "on_model_verified",
        "description": "Writes audit entry for model signature verification",
        "module": "aictl.core.hooks",
        "events": [],
        "audit": ["model.verified", "trust.violation"],
        "wired": True,
        "dispatches": True,
    },
    {
        "name": "on_snapshot_created",
        "description": "Emits snapshot.created event + audit entry",
        "module": "aictl.core.hooks",
        "events": ["snapshot.created"],
        "audit": ["snapshot.created"],
        "wired": True,
        "dispatches": True,
    },
    {
        "name": "on_engine_health_changed",
        "description": "Emits engine.ready or engine.offline event on health change",
        "module": "aictl.core.hooks",
        "events": ["engine.ready", "engine.offline"],
        "audit": [],
        "wired": False,
        "dispatches": True,
    },
    {
        "name": "on_slo_violation",
        "description": "Emits slo.violation event + audit warning",
        "module": "aictl.core.hooks",
        "events": ["slo.violation"],
        "audit": ["slo.violation"],
        "wired": True,
        "dispatches": True,
    },
    {
        "name": "on_proxy_request",
        "description": "Writes audit entry for each proxied inference request",
        "module": "aictl.core.hooks",
        "events": [],
        "audit": ["proxy.request"],
        "wired": False,
        "dispatches": False,
    },
    {
        "name": "on_node_joined",
        "description": "Emits node.joined event + audit entry when a node joins",
        "module": "aictl.core.hooks",
        "events": ["node.joined"],
        "audit": ["node.joined"],
        "wired": False,
        "dispatches": True,
    },
    {
        "name": "on_config_changed",
        "description": "Writes audit entry when configuration is modified",
        "module": "aictl.core.hooks",
        "events": [],
        "audit": ["config.changed"],
        "wired": True,
        "dispatches": True,
    },
]

_HOOK_MAP = {h["name"]: h for h in _HOOKS}


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("hooks", help="Inspect, test, and subscribe to integration hooks")
    hsub = p.add_subparsers(dest="hooks_cmd")

    ls = hsub.add_parser("list", help="List all known hooks")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=run_list)

    test = hsub.add_parser("test", help="Dry-run a hook with sample data")
    test.add_argument("name", help="Hook name (e.g. on_stack_applied)")
    test.add_argument("--live", action="store_true",
                      help="Actually fire subscribed webhooks/scripts (default: suppressed)")
    test.add_argument("--json", action="store_true")
    test.set_defaults(func=run_test)

    emit = hsub.add_parser("emit", help="Emit a test event to the event bus")
    emit.add_argument("event_type", help="Event type (e.g. stack.applied)")
    emit.add_argument("--source", default="test")
    emit.add_argument("--json", action="store_true")
    emit.set_defaults(func=run_emit)

    add = hsub.add_parser(
        "add",
        help="Run a webhook or local script whenever a hook event fires",
    )
    add.add_argument("event_type", help="Event type to react to (e.g. stack.applied), or '*' for all")
    agroup = add.add_mutually_exclusive_group(required=True)
    agroup.add_argument("--webhook", help="HTTP(S) URL to POST the event to")
    agroup.add_argument("--script", help="Absolute path to a local executable to run with the event as JSON on stdin")
    add.add_argument("--json", action="store_true")
    add.set_defaults(func=run_add)

    remove = hsub.add_parser("remove", help="Remove a webhook/script subscription")
    remove.add_argument("event_type")
    rgroup = remove.add_mutually_exclusive_group(required=True)
    rgroup.add_argument("--webhook", help="Webhook URL to remove")
    rgroup.add_argument("--script", help="Script path to remove")
    remove.add_argument("--json", action="store_true")
    remove.set_defaults(func=run_remove)

    subs = hsub.add_parser("subscriptions", help="List configured webhook/script subscriptions")
    subs.add_argument("--json", action="store_true")
    subs.set_defaults(func=run_subscriptions)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_list(args: argparse.Namespace) -> int:
    """List all known hooks with their events, audit actions, and wiring status."""
    rows = [
        {
            "name": h["name"],
            "events": ", ".join(h["events"]) if h["events"] else "(none)",
            "audit": ", ".join(h["audit"]) if h["audit"] else "(none)",
            "wired": "yes" if h["wired"] else "no",
            "dispatches": "yes" if h["dispatches"] else "no",
        }
        for h in _HOOKS
    ]

    if getattr(args, "json", False):
        print_json(_HOOKS)
        return 0

    print_table(rows, ["name", "events", "audit", "wired", "dispatches"])
    n_wired = sum(1 for h in _HOOKS if h["wired"])
    print(f"\n  {len(_HOOKS)} hooks registered in aictl.core.hooks "
          f"({n_wired} wired to a real production call site)")
    return 0


def run_test(args: argparse.Namespace) -> int:
    """Dry-run a hook: call it with sample data and capture events/audit.

    By default, webhook/script dispatch is SUPPRESSED -- this is meant as a
    dry-run to inspect events/audit wiring, not to fire real external
    integrations on every invocation. Pass --live to actually dispatch."""
    from aictl.core.events import get_bus
    from aictl.core import hook_dispatch
    import aictl.core.hooks as hooks_module
    import contextlib

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
    live = getattr(args, "live", False)
    _raw_state_dir = getattr(args, "state_dir", None)
    # AuditLog/hook_dispatch do `(state_dir or DEFAULT) / "..."` -- a bare str
    # (the global --state-dir flag delivers a string) would crash on that
    # division, so coerce to Path here like StateStore already does.
    state_dir = Path(_raw_state_dir) if _raw_state_dir else None
    # Record event bus count before
    bus = get_bus()
    before_count = len(bus.recent(n=500))

    suppress_ctx = contextlib.nullcontext() if live else hook_dispatch.suppress_dispatch()

    # Invoke with sample data. state_dir is always forwarded explicitly so
    # --live dispatches against --state-dir (not silently against the real
    # default state dir regardless of what the caller asked for).
    try:
        with suppress_ctx:
            if hook_name == "on_stack_applied":
                fn("test-stack", file="test.yaml", mode="direct", services=2, state_dir=state_dir)
            elif hook_name == "on_stack_stopped":
                fn("test-stack", state_dir=state_dir)
            elif hook_name == "on_model_registered":
                fn("test-model", digest="sha256:abc", runtime="ollama", state_dir=state_dir)
            elif hook_name == "on_model_verified":
                fn("test-model", method="cosign", valid=True, state_dir=state_dir)
            elif hook_name == "on_snapshot_created":
                fn("snap_001", label="test", state_dir=state_dir)
            elif hook_name == "on_engine_health_changed":
                fn("vllm", status="READY", endpoint="http://localhost:8000", state_dir=state_dir)
            elif hook_name == "on_slo_violation":
                fn("vllm", metric="ttft_p95_ms", value=900.0, threshold=500.0,
                   action="alert", state_dir=state_dir)
            elif hook_name == "on_proxy_request":
                fn(key_name="test-key", model="llama3", engine="vllm", tokens=128, state_dir=state_dir)
            elif hook_name == "on_node_joined":
                fn("node-001", hostname="worker1", address="192.168.1.2", state_dir=state_dir)
            elif hook_name == "on_config_changed":
                fn("log_level", old_value="info", new_value="debug", state_dir=state_dir)
            else:
                err(f"No sample data available for {hook_name}")
                return 1
    except Exception as e:
        err(f"Hook raised exception: {e}")
        return 1

    after_count = len(bus.recent(n=500))
    new_events = after_count - before_count

    n_subs = len(hook_dispatch.load_subscriptions(state_dir))

    result = {
        "hook": hook_name,
        "status": "ok",
        "events_emitted": new_events,
        "expected_events": meta["events"],
        "expected_audit": meta["audit"],
        "dispatch_mode": "live" if live else "suppressed",
        "subscriptions_configured": n_subs,
    }

    if getattr(args, "json", False):
        print_json(result)
        return 0

    ok(f"Hook {hook_name!r} executed successfully")
    print(f"  events emitted        : {new_events}")
    print(f"  expected events       : {', '.join(meta['events']) if meta['events'] else '(none)'}")
    print(f"  expected audit        : {', '.join(meta['audit']) if meta['audit'] else '(none)'}")
    print(f"  dispatch              : {'LIVE (webhooks/scripts really fired)' if live else 'suppressed (dry-run, use --live to fire for real)'}")
    print(f"  webhook/script subs   : {n_subs} configured"
          + (" (aictl hooks add ...)" if n_subs == 0 else ""))
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

    if getattr(args, "json", False):
        print_json({"event_type": event_type, "source": source, "bus_size": after})
        return 0

    ok(f"Emitted {event_type!r} from source={source!r}")
    if after > before:
        print(f"  Event bus now has {after} entries")
    return 0


def run_add(args: argparse.Namespace) -> int:
    """Persist a webhook/script subscription for an event type."""
    from aictl.core.hook_dispatch import add_subscription

    event_type = args.event_type
    kind = "webhook" if getattr(args, "webhook", None) else "script"
    target = getattr(args, "webhook", None) or getattr(args, "script", None)
    state_dir = getattr(args, "state_dir", None)

    try:
        subscription = add_subscription(event_type, kind, target, state_dir=state_dir)
    except ValueError as e:
        err(str(e))
        return 1

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json(asdict(subscription))
        return 0

    ok(f"Subscribed: {event_type!r} -> {kind} {target!r}")
    return 0


def run_remove(args: argparse.Namespace) -> int:
    """Remove a persisted webhook/script subscription."""
    from aictl.core.hook_dispatch import remove_subscription

    event_type = args.event_type
    kind = "webhook" if getattr(args, "webhook", None) else "script"
    target = getattr(args, "webhook", None) or getattr(args, "script", None)
    state_dir = getattr(args, "state_dir", None)

    removed = remove_subscription(event_type, kind, target, state_dir=state_dir)

    if getattr(args, "json", False):
        print_json({"removed": removed})
        return 0

    if removed:
        ok(f"Unsubscribed: {event_type!r} -> {kind} {target!r}")
    else:
        print(f"No matching subscription: {event_type!r} -> {kind} {target!r}")
    return 0


def run_subscriptions(args: argparse.Namespace) -> int:
    """List all persisted webhook/script subscriptions."""
    from dataclasses import asdict
    from aictl.core.hook_dispatch import load_subscriptions

    state_dir = getattr(args, "state_dir", None)
    subs = load_subscriptions(state_dir)

    if getattr(args, "json", False):
        print_json([asdict(s) for s in subs])
        return 0

    if not subs:
        print("No webhook/script subscriptions configured.")
        print("  Add one: aictl hooks add stack.applied --webhook https://example.com/hook")
        return 0

    rows = [{"event_type": s.event_type, "kind": s.kind, "target": s.target,
            "enabled": str(s.enabled)} for s in subs]
    print_table(rows, ["event_type", "kind", "target", "enabled"])
    return 0
