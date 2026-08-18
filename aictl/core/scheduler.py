"""Scheduler: makes persisted batch-job / warmup schedules actually fire.

`aictl batch add --schedule '0 2 * * *'` and `aictl warmup schedule --every 1h`
persist a schedule, but nothing in the codebase ever executed one on its own —
"batch run <job>" and "aictl warmup run" were the only ways anything actually
ran (see docs/FEATURE_GAP_AUDIT.md P3/P4). This module is the missing executor:

  - `run_due_batch_jobs()` re-checks every persisted batch job's cron schedule
    and runs any that are due, reusing the exact execution path
    `aictl batch run` already uses (aictl.cmd.batch._execute_job).
  - `run_due_warmup()` re-checks the single persisted warmup schedule and runs
    it when its `next_run` has passed, reusing `WarmupManager`.
  - `run_due_all()` runs both — this is what `aictl scheduler tick` and the
    daemon's background SchedulerDaemon thread both call.

Cron matching supports the practical subset actually used by `aictl batch`
(exact values, `*`, comma lists, ranges, and `*/N` steps) — stdlib only, no
croniter dependency. `now`/`last_run` are always explicit parameters (never a
bare `time.time()` call buried in the matching logic), so every code path here
is deterministically testable without real sleeping.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from aictl.core.constants import MIN_SCHEDULE_INTERVAL_SECS


def _field_matches(field: str, value: int) -> bool:
    """True if a single cron field ('*', '5', '1,3,5', '1-5', '*/15') matches
    `value`. Malformed sub-parts are skipped (never match), not raised —
    a broken schedule string must degrade to 'never due', not crash the
    scheduler tick for every OTHER job."""
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        try:
            if part.startswith("*/"):
                step = int(part[2:])
                if step > 0 and value % step == 0:
                    return True
            elif "-" in part:
                lo, hi = part.split("-", 1)
                if int(lo) <= value <= int(hi):
                    return True
            else:
                if int(part) == value:
                    return True
        except ValueError:
            continue
    return False


def cron_matches(cron_expr: str, at: time.struct_time) -> bool:
    """True if the 5-field cron expression ('min hour day month weekday')
    matches the given local time. A malformed expression (wrong field count)
    never matches, rather than raising."""
    fields = cron_expr.split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    # struct_time.tm_wday: Monday=0..Sunday=6; cron weekday: Sunday=0..Saturday=6.
    cron_wday = (at.tm_wday + 1) % 7
    return (
        _field_matches(minute, at.tm_min)
        and _field_matches(hour, at.tm_hour)
        and _field_matches(day, at.tm_mday)
        and _field_matches(month, at.tm_mon)
        and _field_matches(weekday, cron_wday)
    )


def _minute_floor(ts: float) -> float:
    """Floor a timestamp to the start of its minute (local time)."""
    lt = time.localtime(ts)
    return ts - lt.tm_sec


def cron_is_due(cron_expr: str, last_run: float | None, now: float) -> bool:
    """True if `cron_expr` matches the current minute AND we have not already
    run during this exact matching minute.

    A tick-based scheduler polls far more often than once a minute (the daemon
    ticks every ~60s, but a manual `scheduler tick` could be run repeatedly in
    quick succession) — without the "already ran this minute" guard, a job
    whose cron matches the current minute would fire on every tick within that
    minute instead of once.
    """
    current_minute = _minute_floor(now)
    if last_run is not None and last_run >= current_minute:
        return False
    return cron_matches(cron_expr, time.localtime(now))


def _batch_db_path(state_dir: Path | None) -> Path:
    # Delegate to cmd.batch's own resolver (single source of truth) rather than
    # re-deriving the same env-var/state-dir logic here, which would silently
    # drift out of sync if that resolution ever changes.
    from aictl.cmd.batch import _db_path
    return _db_path(state_dir)


def _warmup_schedule_path(state_dir: Path | None) -> Path:
    if state_dir:
        return Path(state_dir) / "warmup_schedule.json"
    from aictl.core.state import resolve_state_dir
    return resolve_state_dir() / "warmup_schedule.json"


def run_due_batch_jobs(state_dir: Path | None = None,
                       now: float | None = None) -> list[dict[str, Any]]:
    """Run every persisted batch job whose cron schedule is due.

    Returns one result dict per job that was actually run: {name, success,
    elapsed_s}. Jobs not due are skipped silently (this is a routine tick, not
    an error condition). Reuses `aictl.cmd.batch`'s own job-execution and
    persistence path, so a scheduler-fired run is indistinguishable in
    batch.json from a manual `aictl batch run`.
    """
    now = now if now is not None else time.time()
    import json
    from aictl.core.atomicio import atomic_write_text
    from aictl.cmd.batch import _execute_job

    path = _batch_db_path(state_dir)
    if not path.exists():
        return []
    try:
        db = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(db, dict) or not isinstance(db.get("jobs"), dict):
        return []

    results: list[dict[str, Any]] = []
    for name, job in db["jobs"].items():
        if not isinstance(job, dict):
            continue
        schedule = job.get("schedule", "")
        if not schedule or not cron_is_due(schedule, job.get("last_run"), now):
            continue

        # elapsed_s measures real wall-clock duration of the run — always via
        # time.monotonic(), never derived from the injectable `now` scheduling
        # parameter (which tests may set to an arbitrary fake timestamp).
        t0_mono = time.monotonic()
        success = _execute_job(job)
        elapsed = time.monotonic() - t0_mono
        job["last_run"] = now
        job["last_status"] = "success" if success else "failed"
        job["runs"] = job.get("runs", 0) + 1
        results.append({"name": name, "success": success, "elapsed_s": elapsed})

    if results:
        atomic_write_text(path, json.dumps(db, indent=2, ensure_ascii=False))
    return results


def run_due_warmup(state_dir: Path | None = None,
                   now: float | None = None) -> dict[str, Any] | None:
    """Run the persisted warmup schedule if it is due. Returns a result dict
    ({top, warmed, elapsed_s}) or None if no schedule is configured or it is
    not yet due. Advances `next_run` from `now` (not from the missed
    `next_run`), so a daemon that was stopped for a while does not fire a burst
    of catch-up warmups — it resumes on a fresh interval from whenever it
    actually ran next."""
    now = now if now is not None else time.time()
    import json
    from aictl.core.atomicio import atomic_write_text

    path = _warmup_schedule_path(state_dir)
    if not path.exists():
        return None
    try:
        schedule = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(schedule, dict):
        return None

    next_run = schedule.get("next_run", 0)
    if now < next_run:
        return None

    from aictl.core.state import StateStore
    from aictl.runtime.warmup import WarmupManager

    top = schedule.get("top", 3)
    store = StateStore(state_dir)
    mgr = WarmupManager(store)
    candidates = mgr.get_warmup_candidates(top_n=top)
    warmed = mgr.warmup(candidates) if candidates else []

    # Defense-in-depth: a corrupted/hand-edited/legacy schedule file could
    # still carry a non-positive interval_secs even though the CLI now
    # rejects one at creation time (aictl/cmd/warmup.py's run_schedule). A
    # non-positive value would make next_run <= now forever, busy-firing the
    # warmup on every scheduler tick instead of respecting any interval.
    interval_secs = schedule.get("interval_secs", 3600)
    if not isinstance(interval_secs, (int, float)) or interval_secs < MIN_SCHEDULE_INTERVAL_SECS:
        interval_secs = MIN_SCHEDULE_INTERVAL_SECS
    schedule["next_run"] = now + interval_secs
    schedule["last_run"] = now
    atomic_write_text(path, json.dumps(schedule, indent=2))

    return {"top": top, "warmed": len(warmed), "candidates": len(candidates)}


def run_due_all(state_dir: Path | None = None,
               now: float | None = None) -> dict[str, Any]:
    """Run every due batch job and the warmup schedule (if due). Single entry
    point for `aictl scheduler tick` and the daemon's background thread."""
    now = now if now is not None else time.time()
    return {
        "batch_jobs": run_due_batch_jobs(state_dir, now),
        "warmup": run_due_warmup(state_dir, now),
    }
