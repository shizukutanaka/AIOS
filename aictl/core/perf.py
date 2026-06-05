"""aictl performance instrumentation — automatic per-command timing.

Apple principle: you cannot improve what you do not measure. This module
provides a single decorator and context manager that records latency,
peak memory, and exit status for every CLI command, with zero per-command
boilerplate.

The data is stored under <state_dir>/perf.jsonl. Reading it back is exposed
via `aictl perf` (lazy, may be added later).

Design choices:
  - One file per row (jsonl) so we never lose data on crash
  - Atomic append, no locking required (POSIX append <PIPE_BUF is atomic)
  - Truncate to last 10,000 records on startup (cap disk use)
  - Anonymized — never records command arguments or user content
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterator


@dataclass
class PerfRecord:
    """One performance sample."""
    timestamp: float
    command: str
    duration_ms: float
    exit_code: int
    rss_mb_peak: float
    error_type: str = ""


_PERF_DIR_ENV = "AIOS_STATE_DIR"
_MAX_RECORDS = 10_000
_BUFFER_SIZE = 4096  # max bytes per atomic append


def _perf_path() -> Path:
    """Where we store perf.jsonl."""
    base = os.environ.get(_PERF_DIR_ENV, os.path.expanduser("~/.aios"))
    return Path(base) / "perf.jsonl"


def _measure_rss_mb() -> float:
    """Current RSS in MB. Best-effort; returns 0 if unavailable."""
    try:
        # Linux/macOS: /proc/self/status or resource module
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss is in KB on Linux, bytes on macOS
        if sys.platform == "darwin":
            return usage.ru_maxrss / (1024 * 1024)
        return usage.ru_maxrss / 1024
    except (ImportError, OSError):
        return 0.0


def record(record: PerfRecord) -> None:
    """Append one record to perf.jsonl. Non-blocking, best-effort."""
    try:
        path = _perf_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(record), separators=(",", ":")) + "\n"
        # POSIX guarantees atomic append for writes < PIPE_BUF (typically 4096)
        if len(line.encode("utf-8")) > _BUFFER_SIZE:
            line = line[:_BUFFER_SIZE - 2] + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        # Never let instrumentation crash the program
        pass


@contextmanager
def measure(command: str) -> Iterator[dict[str, Any]]:
    """Context manager that times a block and records perf data.

    Usage:
        with measure("my_command") as ctx:
            do_work()
            ctx["exit_code"] = 0  # set if non-zero on failure
    """
    t0 = time.perf_counter()
    rss_start = _measure_rss_mb()
    ctx: dict[str, Any] = {"exit_code": 0, "error_type": ""}
    try:
        yield ctx
    except BaseException as e:
        ctx["exit_code"] = 1
        ctx["error_type"] = type(e).__name__
        raise
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        rss_end = _measure_rss_mb()
        record(PerfRecord(
            timestamp=time.time(),
            command=command,
            duration_ms=elapsed_ms,
            exit_code=int(ctx.get("exit_code", 0)),
            rss_mb_peak=max(rss_start, rss_end),
            error_type=str(ctx.get("error_type", "")),
        ))


def read_recent(limit: int = 50) -> list[PerfRecord]:
    """Read the most recent perf records. Robust to corrupted lines."""
    path = _perf_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    records = []
    for line in lines[-limit:]:
        try:
            d = json.loads(line)
            records.append(PerfRecord(**d))
        except (json.JSONDecodeError, TypeError):
            continue
    return records


def summary() -> dict[str, Any]:
    """Aggregate stats by command. Useful for `aictl perf`."""
    records = read_recent(limit=1000)
    by_cmd: dict[str, list[PerfRecord]] = {}
    for r in records:
        by_cmd.setdefault(r.command, []).append(r)

    out: dict[str, dict[str, Any]] = {}
    for cmd, rs in by_cmd.items():
        durations = [r.duration_ms for r in rs]
        out[cmd] = {
            "count": len(rs),
            "p50_ms": _percentile(durations, 50),
            "p95_ms": _percentile(durations, 95),
            "p99_ms": _percentile(durations, 99),
            "failures": sum(1 for r in rs if r.exit_code != 0),
        }
    return out


def _percentile(values: list[float], pct: int) -> float:
    """Compute percentile without numpy. values must be a list of numbers."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def truncate_if_needed() -> None:
    """Cap perf.jsonl at _MAX_RECORDS lines. Called on startup."""
    path = _perf_path()
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > _MAX_RECORDS:
            keep = lines[-_MAX_RECORDS:]
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(keep)
    except OSError:
        pass  # best-effort; failure is non-critical
