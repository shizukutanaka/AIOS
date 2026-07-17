"""aictl ps — list running AI services."""

from __future__ import annotations

from typing import Any

import argparse
import json
import subprocess

from aictl.core.output import print_json, print_table
from aictl.core.state import StateStore
from aictl.stack.orchestrator import list_running
from aictl.runtime.broker import detect_container_runtime


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("ps", help="List running AI services")
    p.add_argument("--stack", default="", help="Filter by stack name")
    p.add_argument("--extended", action="store_true",
                   help="Show CPU/memory resource usage")
    p.set_defaults(func=run)


def _fetch_stats(names: list[str]) -> dict[str, dict[str, str]]:
    """Query container runtime for resource stats; returns {name: {cpu, mem}}."""
    if not names:
        return {}
    rt = detect_container_runtime()
    if rt == "none":
        return {}
    try:
        result = subprocess.run(
            [rt, "stats", "--no-stream", "--format", "json"] + names,
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        data = json.loads(result.stdout)
        if not isinstance(data, list):
            data = [data]
        out: dict[str, dict[str, str]] = {}
        for item in data:
            name = item.get("Name", item.get("name", ""))
            out[name] = {
                "cpu": item.get("CPUPerc", item.get("cpu_percent", "")).rstrip("%"),
                "mem": item.get("MemUsage", item.get("memory_usage", "")),
            }
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError,
            ValueError):
        return {}


def run(args: argparse.Namespace) -> int:
    """Execute the ps command."""
    services = list_running(getattr(args, "stack", ""))
    stacks = StateStore(getattr(args, "state_dir", None)).load_stacks()

    extended = getattr(args, "extended", False)
    if extended and services:
        names = [s["name"] for s in services]
        stats = _fetch_stats(names)
        for svc in services:
            s = stats.get(svc["name"], {})
            svc["cpu%"] = s.get("cpu", "")
            svc["mem"] = s.get("mem", "")

    if getattr(args, "json", False):
        print_json({"services": services, "stacks": [s.__dict__ for s in stacks]})
        return 0

    if services:
        cols = ["name", "status", "ports", "container_id"]
        if extended:
            cols += ["cpu%", "mem"]
        print_table(services, cols)
    elif stacks:
        print("No running containers. Applied stacks:")
        for s in stacks:
            print(f"  {s.name} — {s.status} (applied {s.file})")
    else:
        print("No services running. Try: aictl recipe run local-chat")

    return 0
