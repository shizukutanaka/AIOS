"""aictl watch — continuous system monitoring."""

from __future__ import annotations

from typing import Any

import argparse

import time
import os

from aictl.core.state import StateStore
from aictl.core.config import load_config
from aictl.runtime.adapters import discover_engines
from aictl.metrics.slo import read_psi, SLOTarget, check_slo
from aictl.stack.orchestrator import list_running


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("watch", help="Continuous system monitoring")
    p.add_argument("--interval", type=int, default=5, help="Refresh interval (seconds)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the watch command."""
    store = StateStore(getattr(args, "state_dir", None))
    config = load_config(store.dir)
    interval = getattr(args, "interval", 5)

    try:
        while True:
            _render(store, config)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def _render(store: Any, config: Any) -> None:
    """Render one frame of the watch display."""
    os.system('clear' if os.name != 'nt' else 'cls')

    node = store.load_node()
    psi = read_psi()
    services = list_running()
    engines = discover_engines(config.engines.to_dict())
    online = [e for e in engines if e.reachable]

    # Header
    ts = time.strftime("%H:%M:%S")
    print(f"  AI OS Watch — {node.hostname} — {ts}")
    print(f"  Profile: {node.profile} | Services: {len(services)} | Engines: {len(online)}")
    print()

    # PSI
    mem_icon = "\u2713" if psi.memory_some_avg10 < 25 else "\u26a0" if psi.memory_some_avg10 < 50 else "\u2717"
    print(f"  PSI  memory: {mem_icon} {psi.memory_some_avg10:.1f}%  cpu: {psi.cpu_some_avg10:.1f}%  io: {psi.io_some_avg10:.1f}%")

    # Engines
    if online:
        print()
        for e in online:
            models = ", ".join(e.models[:3]) if e.models else "no models"
            print(f"  [{e.engine}] {e.status} — {models} ({e.latency_ms:.0f}ms)")

    # Services
    if services:
        print()
        for s in services:
            print(f"  {s['name']}: {s['status']}")

    # SLO
    target = SLOTarget()
    for e in online:
        from aictl.runtime.adapters import get_adapter
        adapter = get_adapter(e.engine, e.endpoint)
        if adapter:
            try:
                metrics = adapter.scrape_metrics()
                verdict = check_slo(metrics, psi, target)
                icon = "\u2713" if verdict.compliant else "\u2717"
                print(f"\n  SLO {e.engine}: {icon} {'compliant' if verdict.compliant else verdict.action}")
                if not verdict.compliant:
                    for v in verdict.violations[:3]:
                        print(f"    \u2022 {v}")
            except Exception:
                pass  # best-effort; failure is non-critical

    print("\n  Press Ctrl+C to exit")
