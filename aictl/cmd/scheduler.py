"""aictl scheduler — manually trigger a scheduler tick (batch jobs + warmup).

`aictl batch add --schedule ...` and `aictl warmup schedule --every ...` persist
a schedule, but nothing runs it automatically unless the daemon (`aictl serve`)
is running — its background thread calls the same tick this command exposes.
Use this directly if you drive scheduling yourself (a cron job / systemd timer
calling `aictl scheduler tick` on its own cadence), or to test a schedule
without waiting for it.

  aictl scheduler tick          # run whatever is due right now
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from aictl.core.output import ok, print_json


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser(
        "scheduler",
        help="Run due batch jobs / warmup schedules now (what the daemon does in the background).",
    )
    ssub = p.add_subparsers(dest="scheduler_cmd")

    tick = ssub.add_parser("tick", help="Run whatever is due right now.")
    tick.set_defaults(func=run_tick)

    p.set_defaults(func=lambda a: run_tick(a))


def run_tick(args: argparse.Namespace) -> int:
    """Execute one scheduler tick: run every due batch job + the warmup
    schedule if due."""
    from aictl.core.scheduler import run_due_all

    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    result = run_due_all(state_dir)

    if getattr(args, "json", False):
        print_json(result)
        return 0

    jobs = result["batch_jobs"]
    warmup = result["warmup"]
    if not jobs and warmup is None:
        print("Nothing due.")
        return 0

    for j in jobs:
        icon = "✓" if j["success"] else "✗"
        print(f"  {icon} batch job '{j['name']}' ran in {j['elapsed_s']:.1f}s")
    if warmup is not None:
        ok(f"Warmup: warmed {warmup['warmed']}/{warmup['candidates']} model(s)")
    return 0
