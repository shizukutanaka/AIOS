"""aictl log — view structured JSON logs."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json
from aictl.core.logging import get_logger


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("log", help="View structured logs")
    p.add_argument("-n", type=int, default=20, help="Number of entries")
    p.add_argument("--level", default="", choices=["", "debug", "info", "warn", "error"])
    p.add_argument("--rotate", action="store_true", help="Rotate old logs")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the log command."""
    logger = get_logger("aictl")

    if getattr(args, "rotate", False):
        removed = logger.rotate(max_days=7)
        ok(f"Rotated {removed} old log files")
        return 0

    entries = logger.read_logs(n=getattr(args, "n", 20),
                               level=getattr(args, "level", ""))

    if getattr(args, "json", False):
        print_json(entries)
        return 0

    if not entries:
        print("No log entries. Logs are written to ~/.aios/logs/")
        return 0

    for e in entries:
        ts = e.get("ts", "")[:19]
        level = e.get("level", "?").upper()[:4]
        msg = e.get("msg", "")
        extra = {k: v for k, v in e.items()
                 if k not in ("ts", "level", "logger", "msg", "node_id", "correlation_id")}
        extra_str = f" {extra}" if extra else ""
        print(f"  {ts} [{level:4s}] {msg}{extra_str}")

    return 0
