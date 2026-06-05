"""aictl self-healing — auto-recover from known transient failures.

Apple principle: a brittle product makes the user feel stupid. A robust
product makes them feel powerful. Most failures we see are predictable
patterns: OOM, port-in-use, missing-dir, transient-network. The system
should try a recovery before bothering the user.

Usage:
    from aictl.core.self_heal import try_heal

    try:
        risky_operation()
    except Exception as e:
        if not try_heal(e, context={"model": "llama3:8b"}):
            raise

Each healer:
  - Identifies failure by signature (exception type + message pattern)
  - Attempts ONE remediation
  - Returns True on success, False otherwise
  - Logs every attempt for transparency

Healers are intentionally small. If a fix is risky, we don't auto-apply
it; we surface it as a suggestion in the error message instead.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


# ─── Healer registry ────────────────────────────────────────────

@dataclass
class HealAttempt:
    """One self-heal attempt, logged for audit."""
    timestamp: float
    pattern: str
    succeeded: bool
    detail: str = ""


_HISTORY: list[HealAttempt] = []
_HISTORY_LIMIT = 100


def _record(attempt: HealAttempt) -> None:
    """Execute record."""
    _HISTORY.append(attempt)
    if len(_HISTORY) > _HISTORY_LIMIT:
        del _HISTORY[: len(_HISTORY) - _HISTORY_LIMIT]


def get_history() -> list[HealAttempt]:
    """Return a copy of recent heal attempts (for debugging/observability)."""
    return list(_HISTORY)


def clear_history() -> None:
    """Reset history. Mostly for tests."""
    _HISTORY.clear()


# ─── Pattern matchers ───────────────────────────────────────────

def _matches(e: BaseException, *substrings: str) -> bool:
    """True if any substring appears in str(e) or e's class name."""
    msg = (str(e) + " " + type(e).__name__).lower()
    return any(s.lower() in msg for s in substrings)


# ─── Individual healers ─────────────────────────────────────────

def _heal_port_in_use(e: BaseException, context: dict[str, Any]) -> bool:
    """Find a free port near the requested one."""
    if not _matches(e, "address already in use", "port", "EADDRINUSE"):
        return False

    requested = context.get("port")
    if not isinstance(requested, int):
        return False

    # Try the next 50 ports
    for offset in range(1, 51):
        candidate = requested + offset
        if _is_port_free(candidate):
            context["port"] = candidate
            context["healed_from"] = requested
            _record(HealAttempt(
                timestamp=time.time(),
                pattern="port_in_use",
                succeeded=True,
                detail=f"shifted {requested} → {candidate}",
            ))
            return True

    _record(HealAttempt(
        timestamp=time.time(),
        pattern="port_in_use",
        succeeded=False,
        detail=f"no free port near {requested}",
    ))
    return False


def _is_port_free(port: int) -> bool:
    """True if we can bind to the given TCP port on localhost."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False


def _heal_missing_state_dir(e: BaseException, context: dict[str, Any]) -> bool:
    """Create missing state directories on demand."""
    if not _matches(e, "no such file", "FileNotFoundError", "missing"):
        return False

    # Pull a candidate path out of the exception or context
    candidate = None
    if isinstance(e, FileNotFoundError) and getattr(e, "filename", None):
        candidate = e.filename
    elif "path" in context:
        candidate = context["path"]

    if not candidate:
        return False

    p = Path(candidate)
    parent = p.parent if p.suffix else p
    try:
        parent.mkdir(parents=True, exist_ok=True)
        _record(HealAttempt(
            timestamp=time.time(),
            pattern="missing_state_dir",
            succeeded=True,
            detail=f"created {parent}",
        ))
        return True
    except OSError as create_err:
        _record(HealAttempt(
            timestamp=time.time(),
            pattern="missing_state_dir",
            succeeded=False,
            detail=f"could not create {parent}: {create_err}",
        ))
        return False


def _heal_oom_context_shrink(e: BaseException, context: dict[str, Any]) -> bool:
    """Halve max_model_len on OOM. Caller must retry."""
    if not _matches(e, "out of memory", "OOM", "CUDA error: out of memory",
                    "memory exceeded"):
        return False

    current_len = context.get("max_model_len")
    if not isinstance(current_len, int) or current_len <= 4096:
        # Already at minimum, can't shrink further
        _record(HealAttempt(
            timestamp=time.time(),
            pattern="oom_context_shrink",
            succeeded=False,
            detail="already at minimum context length",
        ))
        return False

    new_len = max(4096, current_len // 2)
    context["max_model_len"] = new_len
    context["healed_from_max_len"] = current_len
    _record(HealAttempt(
        timestamp=time.time(),
        pattern="oom_context_shrink",
        succeeded=True,
        detail=f"halved max_model_len {current_len} → {new_len}",
    ))
    return True


def _heal_transient_network(e: BaseException, context: dict[str, Any]) -> bool:
    """For connection refused / timeout, suggest a single retry."""
    if not _matches(e, "ConnectionRefused", "TimeoutError",
                    "connection reset", "EOF occurred"):
        return False

    attempts = context.get("network_retries", 0)
    if attempts >= 2:
        _record(HealAttempt(
            timestamp=time.time(),
            pattern="transient_network",
            succeeded=False,
            detail=f"already retried {attempts} times",
        ))
        return False

    context["network_retries"] = attempts + 1
    # Brief backoff (10ms first attempt, 100ms second)
    time.sleep(0.01 * (10 ** attempts))
    _record(HealAttempt(
        timestamp=time.time(),
        pattern="transient_network",
        succeeded=True,
        detail=f"backoff retry #{attempts + 1}",
    ))
    return True


# ─── Master healer registry ─────────────────────────────────────

# Order matters: try cheap heals first, then more invasive ones.
HEALERS: list[Callable[[BaseException, dict[str, Any]], bool]] = [
    _heal_transient_network,
    _heal_missing_state_dir,
    _heal_port_in_use,
    _heal_oom_context_shrink,
]


def try_heal(e: BaseException, context: dict[str, Any] | None = None) -> bool:
    """Try every applicable healer. Return True if any succeeded.

    The `context` dict is mutated in-place when a healer modifies parameters
    (e.g. shifts a port, halves max_model_len). The caller should retry the
    failed operation using the updated context.
    """
    if context is None:
        context = {}

    for healer in HEALERS:
        try:
            if healer(e, context):
                return True
        except Exception as healer_error:
            # A healer must never make things worse. Log and move on.
            _record(HealAttempt(
                timestamp=time.time(),
                pattern=healer.__name__,
                succeeded=False,
                detail=f"healer crashed: {healer_error}",
            ))
            continue
    return False


def with_self_heal(
    operation: Callable[..., Any],
    context: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> Any:
    """Run an operation with automatic self-heal retry.

    Each failure → try heal → if healed, retry. Up to `max_retries` retries.
    Re-raises the last exception if all attempts fail.
    """
    if context is None:
        context = {}

    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return operation(**context) if context else operation()
        except Exception as e:
            last_exc = e
            if attempt >= max_retries:
                break
            if not try_heal(e, context):
                break  # No applicable healer → don't keep retrying
            # else: loop, retry with potentially modified context

    if last_exc is not None:
        raise last_exc
