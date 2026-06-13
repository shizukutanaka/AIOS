"""aictl alert — Prometheus SLO alert rules management and history."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, print_json, print_kv, print_table
from aictl.metrics.alerts import generate_alert_rules
from aictl.core.events import emit as emit_event, get_bus


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("alert", help="SLO alert rules management and history")
    asub = p.add_subparsers(dest="alert_cmd")

    rules = asub.add_parser("rules", help="Show Prometheus alerting rules for current SLO targets")
    rules.add_argument("--yaml", action="store_true", help="Output raw YAML (default)")
    rules.set_defaults(func=run_rules)

    test = asub.add_parser("test", help="Dry-run evaluate an alert rule name")
    test.add_argument("rule", help="Alert rule name (e.g. AIOSEngineDown)")
    test.set_defaults(func=run_test)

    silence = asub.add_parser("silence", help="Record a silence window (event bus only)")
    silence.add_argument("--duration", default="1h", help="Duration string (e.g. 30m, 2h)")
    silence.add_argument("--reason", default="", help="Reason for silence")
    silence.set_defaults(func=run_silence)

    history = asub.add_parser("history", help="Show recent alert events from the event bus")
    history.add_argument("-n", "--last", type=int, default=20, help="Number of events to show")
    history.set_defaults(func=run_history)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


# Known alert rule names (match generate_alert_rules output)
_RULE_NAMES = [
    "AIOSEngineDown",
    "AIOSHighErrorRate",
    "AIOSQueueDepthHigh",
    "AIOSKVCacheSaturated",
    "AIOSThroughputLow",
    "AIOSMemoryPressureHigh",
]


def run_rules(args: argparse.Namespace) -> int:
    """Show all Prometheus alerting rules for the current SLO configuration."""
    yaml_text = generate_alert_rules()

    if getattr(args, "json", False):
        # Parse the YAML-like text into a list of rule dicts for JSON consumers
        rules = []
        current: dict = {}
        for line in yaml_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- alert:"):
                if current:
                    rules.append(current)
                current = {"alert": stripped.split(":", 1)[1].strip()}
            elif stripped.startswith("expr:"):
                current["expr"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("for:"):
                current["for"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("severity:"):
                current["severity"] = stripped.split(":", 1)[1].strip()
        if current:
            rules.append(current)
        print_json({"rule_count": len(rules), "rules": rules})
        return 0

    print(yaml_text)
    return 0


def run_test(args: argparse.Namespace) -> int:
    """Dry-run check whether a named alert rule is valid."""
    rule = args.rule
    yaml_text = generate_alert_rules()

    if rule not in yaml_text:
        err(f"Rule not found: {rule}")
        if getattr(args, "json", False):
            print_json({"found": False, "rule": rule, "known_rules": _RULE_NAMES})
        return 1

    if getattr(args, "json", False):
        print_json({"found": True, "rule": rule, "status": "valid"})
        return 0

    ok(f"Rule found: {rule}")
    # Extract the rule block from the YAML
    lines = yaml_text.splitlines()
    in_rule = False
    for line in lines:
        if f"- alert: {rule}" in line:
            in_rule = True
        if in_rule:
            print(line)
            if in_rule and line.strip().startswith("summary:"):
                break
    return 0


def run_silence(args: argparse.Namespace) -> int:
    """Record a silence window on the event bus."""
    import time

    duration_str = getattr(args, "duration", "1h")
    reason = getattr(args, "reason", "")

    # Parse simple duration strings
    mult = {"m": 60, "h": 3600, "d": 86400}
    try:
        unit = duration_str[-1]
        n = int(duration_str[:-1])
        seconds = n * mult.get(unit, 60)
    except (ValueError, KeyError):
        seconds = 3600

    expires_at = time.time() + seconds
    emit_event("alert.silence.started", source="aictl-alert",
               duration=duration_str, reason=reason, expires_at=expires_at)

    if getattr(args, "json", False):
        print_json({
            "silenced": True,
            "duration": duration_str,
            "expires_at": expires_at,
            "reason": reason,
        })
        return 0

    ok(f"Alert silence recorded for {duration_str}")
    if reason:
        print(f"  Reason: {reason}")
    return 0


def run_history(args: argparse.Namespace) -> int:
    """Show recent alert-related events from the event bus."""
    import time as _time

    bus = get_bus()
    n = getattr(args, "last", 20)
    all_events = bus.recent(n=500)

    alert_events = [e for e in all_events
                    if "alert" in getattr(e, "type", "").lower()
                    or "slo" in getattr(e, "type", "").lower()
                    or "violation" in getattr(e, "type", "").lower()]
    alert_events = alert_events[-n:]

    if getattr(args, "json", False):
        print_json([{"type": e.type, "source": e.source,
                     "ts": e.timestamp} for e in alert_events])
        return 0

    if not alert_events:
        print("No alert events in history.")
        return 0

    rows = [{"time": _time.strftime("%H:%M:%S", _time.localtime(e.timestamp)),
             "type": e.type, "source": e.source}
            for e in alert_events]
    print_table(rows, ["time", "type", "source"])
    return 0
