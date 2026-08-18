"""Hook dispatch: turn integration-hook events into real actions.

Before this module (docs/FEATURE_GAP_LIST.md item 18), core/hooks.py's on_*
functions emitted an in-process event and wrote an audit-log entry, but
nothing ever ran a user-configured script or called a webhook — "aictl
hooks" could only inspect hook metadata, never dispatch anything. This is
the missing dispatcher: persisted subscriptions (hooks_subscriptions.json)
map an event_type (or "*" for every event) to either a webhook URL or a
local script path. `dispatch()` is called by every on_* hook right after its
existing emit()/audit() calls.

A broken/unreachable webhook or script must never break the real operation
that triggered the hook (applying a stack, registering a model, ...) — same
philosophy as EventBus.publish, which already swallows listener exceptions.
`dispatch()` therefore never raises; failures are reported in its return
value AND written to the audit log as a discoverable trail.

Security: webhook targets are restricted to http/https. urllib.request's
default opener also handles file:// URLs, which would let a locally
misconfigured "webhook" target read arbitrary local files as part of the
POST attempt — rejected at both subscribe-time and dispatch-time. Script
targets must be an absolute path — a bare name would resolve against
whatever PATH the daemon/CLI process happens to have.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aictl.core.atomicio import atomic_write_text
from aictl.core.constants import HOOK_SCRIPT_TIMEOUT, HOOK_WEBHOOK_TIMEOUT

_VALID_KINDS = ("webhook", "script")

# Test-only escape hatch: `aictl hooks test` must not fire real webhooks/
# scripts on every dry-run invocation. Suppressed via suppress_dispatch();
# NOT part of persisted state, never touched outside that context manager.
_suppressed = False


class suppress_dispatch:
    """Context manager: dispatch() becomes a no-op (returns []) while active.

    Used by `aictl hooks test` (without --live) so a "dry run" of a hook
    can't silently POST to a production webhook or execute a real script.
    """

    def __enter__(self) -> "suppress_dispatch":
        global _suppressed
        self._prev = _suppressed
        _suppressed = True
        return self

    def __exit__(self, *exc: Any) -> None:
        global _suppressed
        _suppressed = self._prev


@dataclass
class HookSubscription:
    event_type: str      # exact event type (e.g. "stack.applied"), or "*" for all
    kind: str             # "webhook" | "script"
    target: str           # URL (webhook) or absolute executable path (script)
    enabled: bool = True


def _subscriptions_path(state_dir: Path | None = None) -> Path:
    if state_dir:
        return Path(state_dir) / "hooks_subscriptions.json"
    from aictl.core.state import resolve_state_dir
    return resolve_state_dir() / "hooks_subscriptions.json"


def load_subscriptions(state_dir: Path | None = None) -> list[HookSubscription]:
    """Load persisted subscriptions, degrading to [] on any corruption (V7:
    a hand-edited/corrupt file must never crash the caller's real
    operation)."""
    path = _subscriptions_path(state_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("subscriptions"), list):
        return []
    subs = []
    for item in data["subscriptions"]:
        if not isinstance(item, dict):
            continue
        try:
            subs.append(HookSubscription(
                event_type=str(item["event_type"]),
                kind=str(item["kind"]),
                target=str(item["target"]),
                enabled=bool(item.get("enabled", True)),
            ))
        except KeyError:
            continue
    return subs


def _save_subscriptions(subs: list[HookSubscription], state_dir: Path | None = None) -> None:
    path = _subscriptions_path(state_dir)
    body = json.dumps({"subscriptions": [asdict(s) for s in subs]}, indent=2)
    # 0o600: this file is an execution manifest (script paths get run with
    # the CLI's own privileges) — owner-only, like the API-keys file.
    atomic_write_text(path, body, mode=0o600)


def validate_target(kind: str, target: str) -> str | None:
    """Return an error message if (kind, target) is invalid, else None."""
    if kind not in _VALID_KINDS:
        return f"kind must be one of {_VALID_KINDS} (got {kind!r})"
    if not target:
        return "target must not be empty"
    if kind == "webhook":
        from urllib.parse import urlparse
        scheme = urlparse(target).scheme
        if scheme not in ("http", "https"):
            return f"webhook target must be http:// or https:// (got scheme {scheme!r})"
    if kind == "script":
        if not Path(target).is_absolute():
            return f"script target must be an absolute path (got {target!r})"
    return None


def add_subscription(event_type: str, kind: str, target: str,
                     state_dir: Path | None = None) -> HookSubscription:
    """Persist a new subscription. Raises ValueError on an invalid
    (kind, target) pair -- the CLI turns this into a clear error message
    rather than silently persisting something that can never fire."""
    if not event_type:
        raise ValueError("event_type must not be empty")
    error = validate_target(kind, target)
    if error:
        raise ValueError(error)

    subs = load_subscriptions(state_dir)
    for s in subs:
        if s.event_type == event_type and s.kind == kind and s.target == target:
            s.enabled = True
            _save_subscriptions(subs, state_dir)
            return s

    sub = HookSubscription(event_type=event_type, kind=kind, target=target, enabled=True)
    subs.append(sub)
    _save_subscriptions(subs, state_dir)
    return sub


def remove_subscription(event_type: str, kind: str, target: str,
                        state_dir: Path | None = None) -> bool:
    """Remove a matching subscription. Returns True if one was removed."""
    subs = load_subscriptions(state_dir)
    remaining = [s for s in subs
                if not (s.event_type == event_type and s.kind == kind and s.target == target)]
    if len(remaining) == len(subs):
        return False
    _save_subscriptions(remaining, state_dir)
    return True


def _run_webhook(target: str, event_type: str, data: dict[str, Any]) -> None:
    error = validate_target("webhook", target)
    if error:
        raise ValueError(error)
    body = json.dumps({"event_type": event_type, "data": data}, default=str).encode("utf-8")
    req = urllib.request.Request(
        target, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HOOK_WEBHOOK_TIMEOUT) as resp:
        resp.read()


def _run_script(target: str, event_type: str, data: dict[str, Any]) -> None:
    payload = json.dumps({"event_type": event_type, "data": data}, default=str)
    # No shell=True and an argv list (not a string): the event_type/target
    # come from local config the user set themselves, but this still avoids
    # any shell-metacharacter surprises regardless of trust level.
    result = subprocess.run(
        [target, event_type], input=payload, capture_output=True, text=True,
        timeout=HOOK_SCRIPT_TIMEOUT, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"script exited {result.returncode}: {result.stderr[:200]}")


def dispatch(event_type: str, state_dir: Path | None = None,
            **data: Any) -> list[dict[str, Any]]:
    """Run every enabled subscription matching event_type (or subscribed to
    "*"). Never raises -- per-subscription failures are reported in the
    returned list AND written to the audit log, so a broken webhook/script
    can never break the real operation that triggered the hook, while still
    leaving a discoverable trail."""
    if _suppressed:
        return []

    subs = load_subscriptions(state_dir)
    results: list[dict[str, Any]] = []
    for sub in subs:
        if not sub.enabled:
            continue
        if sub.event_type != "*" and sub.event_type != event_type:
            continue
        entry: dict[str, Any] = {"kind": sub.kind, "target": sub.target}
        try:
            if sub.kind == "webhook":
                _run_webhook(sub.target, event_type, data)
            elif sub.kind == "script":
                _run_script(sub.target, event_type, data)
            else:
                raise ValueError(f"unknown subscription kind {sub.kind!r}")
            entry["ok"] = True
        except Exception as e:
            # Broad catch is deliberate: a non-executable script raises
            # PermissionError, a bad host raises URLError, a timeout raises
            # subprocess.TimeoutExpired/socket.timeout -- none of these may
            # ever propagate into the caller's real operation.
            entry["ok"] = False
            entry["error"] = str(e)[:200]
            from aictl.core.audit import audit
            audit("hook.dispatch", resource=sub.target, action=sub.kind,
                  outcome="failure", state_dir=state_dir,
                  event_type=event_type, error=entry["error"])
        results.append(entry)
    return results
