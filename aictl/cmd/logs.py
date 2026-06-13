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
    p.add_argument("--since", default="", help="Show logs since duration (e.g. 5m, 1h, 2d)")
    p.add_argument("--level", default="", help="Filter by log level (ERROR, WARN, INFO, DEBUG)")
    p.add_argument("--grep", default="", help="Filter log lines by pattern")
    p.set_defaults(func=run)


def _parse_since(since: str) -> str:
    """Convert relative duration to a Go duration string understood by podman/docker.

    Accepts: Ns, Nm, Nh, Nd  (seconds/minutes/hours/days)
    Returns the value unchanged if it doesn't match, letting the runtime reject it.
    """
    if not since:
        return ""
    since = since.strip()
    # podman/docker accept e.g. "5m", "1h", "2d", "30s" natively
    return since


def run(args: argparse.Namespace) -> int:
    """Execute the logs command."""
    rt = detect_container_runtime()
    if rt == "none":
        err("No container runtime found")
        return 1

    name = args.service
    if not name.startswith("aios-"):
        name = f"aios-{name}"

    cmd = [rt, "logs", "--tail", args.tail]
    if args.follow:
        cmd.append("-f")
    since = _parse_since(getattr(args, "since", ""))
    if since:
        cmd += ["--since", since]
    cmd.append(name)

    level = getattr(args, "level", "").upper()
    grep = getattr(args, "grep", "")
    needs_filter = bool(level or grep)

    try:
        if needs_filter:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=None if args.follow else 10)
            lines = (proc.stdout + proc.stderr).splitlines()
            for line in lines:
                if level and level not in line.upper():
                    continue
                if grep and grep not in line:
                    continue
                print(line)
            return proc.returncode
        else:
            proc = subprocess.run(cmd, timeout=None if args.follow else 10)
            return proc.returncode
    except subprocess.TimeoutExpired:
        return 0
    except FileNotFoundError:
        err(f"{rt} not found")
        return 1
    except KeyboardInterrupt:
        return 0
