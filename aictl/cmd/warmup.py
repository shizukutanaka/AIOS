"""aictl warmup — preload frequently used models."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json, print_table
from aictl.core.state import StateStore
from aictl.runtime.warmup import WarmupManager


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("warmup", help="Preload frequently used models")
    wsub = p.add_subparsers(dest="warmup_cmd")

    run_p = wsub.add_parser("run", help="Execute warmup")
    run_p.add_argument("--top", type=int, default=3, help="Number of models to warm up")
    run_p.set_defaults(func=run_warmup)

    stats = wsub.add_parser("stats", help="Show model usage statistics")
    stats.set_defaults(func=run_stats)

    schedule = wsub.add_parser("schedule", help="Set up a recurring warmup schedule")
    schedule.add_argument("--every", default="1h",
                          help="Interval (e.g. 30m, 1h, 6h)")
    schedule.add_argument("--top", type=int, default=3,
                          help="Number of top models to warm")
    schedule.set_defaults(func=run_schedule)

    cancel = wsub.add_parser("cancel", help="Cancel the warmup schedule")
    cancel.set_defaults(func=run_cancel)

    status = wsub.add_parser("status", help="Show current warmup schedule and next run")
    status.set_defaults(func=run_schedule_status)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_warmup(args: argparse.Namespace) -> int:
    """Execute the warmup subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = WarmupManager(store)
    candidates = mgr.get_warmup_candidates(top_n=getattr(args, "top", 3))

    if not candidates:
        print("No model usage history. Use models first, then run warmup.")
        return 0

    if getattr(args, "json", False):
        print_json([{"model": c.model, "engine": c.engine, "count": c.count} for c in candidates])
        return 0

    ok(f"Warming up {len(candidates)} models...")
    results = mgr.warmup(candidates)
    for r in results:
        icon = "\u2713" if r.get("status") == "loaded" else "\u2717"
        lt = f" ({r['load_time_ms']:.0f}ms)" if "load_time_ms" in r else ""
        print(f"  {icon} {r['model']} [{r['engine']}] — {r['status']}{lt}")
    return 0


def _schedule_path(args: argparse.Namespace):
    from pathlib import Path
    state_dir = getattr(args, "state_dir", None)
    if state_dir:
        return Path(state_dir) / "warmup_schedule.json"
    from aictl.core.state import DEFAULT_STATE_DIR
    return DEFAULT_STATE_DIR / "warmup_schedule.json"


def _parse_interval_secs(interval: str) -> int:
    mult = {"m": 60, "h": 3600, "d": 86400}
    try:
        unit = interval[-1]
        n = int(interval[:-1])
        return n * mult.get(unit, 3600)
    except (ValueError, KeyError):
        return 3600


def run_schedule(args: argparse.Namespace) -> int:
    """Set up a recurring warmup schedule."""
    import json as _json, time as _time
    from pathlib import Path

    interval = getattr(args, "every", "1h")
    top = getattr(args, "top", 3)
    secs = _parse_interval_secs(interval)
    next_run = _time.time() + secs

    schedule = {
        "every": interval,
        "interval_secs": secs,
        "top": top,
        "created_at": _time.time(),
        "next_run": next_run,
    }
    path = _schedule_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(schedule, indent=2))

    if getattr(args, "json", False):
        print_json(schedule)
        return 0

    ok(f"Warmup scheduled: every {interval}, top {top} models")
    return 0


def run_cancel(args: argparse.Namespace) -> int:
    """Cancel the warmup schedule."""
    path = _schedule_path(args)
    if not path.exists():
        print("No warmup schedule configured.")
        return 0
    path.unlink()
    ok("Warmup schedule cancelled")
    return 0


def run_schedule_status(args: argparse.Namespace) -> int:
    """Show current warmup schedule and next run estimate."""
    import json as _json, time as _time
    from aictl.core.output import print_kv

    path = _schedule_path(args)
    if not path.exists():
        print("No warmup schedule configured. Use: aictl warmup schedule --every 1h")
        return 0

    try:
        schedule = _json.loads(path.read_text())
    except (ValueError, OSError):
        print("Schedule file is corrupt.")
        return 1

    next_run = schedule.get("next_run", 0)
    now = _time.time()
    remaining = max(0, next_run - now)
    remaining_str = (f"{int(remaining//3600)}h {int((remaining%3600)//60)}m"
                     if remaining > 60 else f"{int(remaining)}s")

    if getattr(args, "json", False):
        print_json({**schedule, "remaining_secs": int(remaining)})
        return 0

    ok("Warmup Schedule")
    print_kv([
        ("interval",    schedule.get("every", "?")),
        ("top models",  str(schedule.get("top", 3))),
        ("next run in", remaining_str),
    ], indent=2)
    return 0


def run_stats(args: argparse.Namespace) -> int:
    """Execute the stats subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = WarmupManager(store)
    candidates = mgr.get_warmup_candidates(top_n=20)

    if getattr(args, "json", False):
        print_json([{"model": c.model, "engine": c.engine, "count": c.count,
                     "avg_load_ms": c.avg_load_time_ms} for c in candidates])
        return 0

    if not candidates:
        print("No model usage history yet.")
        return 0

    rows = [{"model": c.model, "engine": c.engine, "uses": c.count,
             "avg_load": f"{c.avg_load_time_ms:.0f}ms"} for c in candidates]
    print_table(rows, ["model", "engine", "uses", "avg_load"])
    return 0
