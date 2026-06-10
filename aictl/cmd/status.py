"""aictl status — unified system status view."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import err, print_json, print_kv
from aictl.core.state import StateStore
from aictl.core.config import load_config
from aictl.runtime.broker import full_detect
from aictl.runtime.adapters import discover_engines
from aictl.runtime.nodes import NodeManager
from aictl.stack.orchestrator import list_running
from aictl.metrics.slo import read_psi


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("status", help="Unified system status")
    p.add_argument(
        "--brief",
        action="store_true",
        help="One-line health summary only (good for shells, prompts, watchdogs).",
    )
    p.add_argument("--watch", action="store_true",
                   help="Continuously refresh status (like watch(1))")
    p.add_argument("--interval", type=int, default=5,
                   help="Refresh interval in seconds when --watch is active (default: 5)")
    p.set_defaults(func=run)


def _build_one_liner(store: Any, report: Any, services: Any, psi: Any, engines_online: int) -> tuple[str, str]:
    """Return (icon, text). One line, parseable, suitable for prompts.

    Format: "<icon> <profile> | <GPUs> GPU | <services> svc | <engines> eng | mem <pct>%"
    Icon: ✓ healthy, ⚠ degraded, ✗ down
    """
    issues = list(report.issues or [])
    mem = float(getattr(psi, "memory_some_avg10", 0) or 0)

    if not store.is_initialized():
        return ("\u2717", "not initialized — run: aictl init")

    if issues or mem >= 50 or engines_online == 0 and len(services) > 0:
        icon = "\u26a0"
    elif mem >= 25:
        icon = "\u26a0"
    else:
        icon = "\u2713"

    gpu_n = len(report.gpus)
    vram_gb = sum(g.vram_mb for g in report.gpus) / 1024 if report.gpus else 0
    gpu_str = f"{gpu_n} GPU ({vram_gb:.0f}GB)" if gpu_n else "CPU only"

    parts = [
        report.profile,
        gpu_str,
        f"{len(services)} svc",
        f"{engines_online} engines",
        f"mem {mem:.0f}%",
    ]
    return (icon, " | ".join(parts))


def run(args: argparse.Namespace) -> int:
    """Execute the status command."""
    if getattr(args, "watch", False):
        return _run_watch(args)
    return _run_once(args)


def _run_watch(args: argparse.Namespace) -> int:
    """Continuously refresh status until Ctrl-C."""
    import time
    import os
    interval = max(1, getattr(args, "interval", 5))
    try:
        while True:
            os.system("clear" if os.name != "nt" else "cls")
            _run_once(args)
            print(f"\n  Refreshing every {interval}s — Ctrl-C to stop")
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    return 0


def _run_once(args: argparse.Namespace) -> int:
    """Single-shot status render (extracted from original run)."""
    store = StateStore(getattr(args, "state_dir", None))
    config = load_config(store.dir)
    node = store.load_node()
    report = full_detect()
    stacks = store.load_stacks()
    services = list_running()
    psi = read_psi()

    engines = discover_engines(config.engines.to_dict())
    engines_online = sum(1 for e in engines if e.reachable)

    if getattr(args, "json", False):
        from dataclasses import asdict
        icon, summary = _build_one_liner(store, report, services, psi, engines_online)
        print_json({
            "summary": summary,
            "healthy": icon == "\u2713",
            "node": asdict(node),
            "profile": report.profile,
            "gpus": len(report.gpus),
            "services": len(services),
            "stacks": len(stacks),
            "engines_online": engines_online,
            "psi_memory": psi.memory_some_avg10,
            "issues": list(report.issues or []),
        })
        return 0

    icon, summary = _build_one_liner(store, report, services, psi, engines_online)

    # Brief mode: just the one-liner, designed for shell prompts, status bars, etc.
    if getattr(args, "brief", False):
        print(f"{icon} {summary}")
        return 0 if icon == "\u2713" else 1

    # Full mode: leading one-liner, then the existing detail.
    print(f"{icon} AI OS — {node.hostname or 'not initialized'}")
    print(f"  {summary}")
    print()

    # Quick stats
    print_kv([
        ("Profile", report.profile),
        ("GPUs", f"{len(report.gpus)} ({sum(g.vram_mb for g in report.gpus)} MB VRAM)"),
        ("RAM", f"{report.system.ram_total_mb} MB"),
        ("Container RT", report.container_runtime or "none"),
        ("Stacks", str(len(stacks))),
        ("Services", str(len(services))),
    ])

    # PSI
    if report.system.psi_enabled:
        print()
        mem_status = "ok" if psi.memory_some_avg10 < 25 else "warn"
        psi_icon = "\u2713" if mem_status == "ok" else "\u26a0"
        print(f"  {psi_icon} Memory pressure: {psi.memory_some_avg10:.1f}% (some avg10)")

    # Engines
    online = [e for e in engines if e.reachable]
    if online:
        print(f"\n  Engines online: {', '.join(e.engine for e in online)}")
    else:
        print("\n  Engines: none reachable")

    # Cluster
    mgr = NodeManager(store)
    cs = mgr.load_cluster()
    if cs.peers:
        active = sum(1 for p in cs.peers if p.status == "active")
        print(f"  Cluster: {cs.mode} ({active}/{len(cs.peers)} peers)")

    # Issues
    if report.issues:
        print()
        for issue in report.issues:
            err(f"  {issue}")

    return 0
