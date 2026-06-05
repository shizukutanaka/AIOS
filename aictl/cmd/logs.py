"""aictl logs — view logs for running services."""

from __future__ import annotations

from typing import Any

import argparse

import subprocess

from aictl.core.output import err
from aictl.runtime.broker import detect_container_runtime


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("logs", help="View service logs")
    p.add_argument("service", help="Service name (e.g. aios-local-chat-llm)")
    p.add_argument("-f", "--follow", action="store_true", help="Follow log output")
    p.add_argument("-n", "--tail", default="50", help="Number of lines (default: 50)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the logs command."""
    rt = detect_container_runtime()
    if rt == "none":
        err("No container runtime found")
        return 1

    name = args.service
    # Auto-prefix if needed
    if not name.startswith("aios-"):
        name = f"aios-{name}"

    cmd = [rt, "logs", "--tail", args.tail]
    if args.follow:
        cmd.append("-f")
    cmd.append(name)

    try:
        proc = subprocess.run(cmd, timeout=None if args.follow else 10)
        return proc.returncode
    except subprocess.TimeoutExpired:
        return 0
    except FileNotFoundError:
        err(f"{rt} not found")
        return 1
    except KeyboardInterrupt:
        return 0
