"""aictl health — comprehensive one-shot system health check.

Combines: hardware, security, fabric, engines, daemon, and tests
into a single pass/fail report with a health score.
"""

from __future__ import annotations

from typing import Any

import argparse
from aictl.core.constants import DAEMON_HOST, DAEMON_PORT

import socket
from aictl.core.output import print_json
from aictl.core.state import StateStore
from aictl.runtime.broker import full_detect


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("health", help="Comprehensive system health check")
    p.add_argument("--wait", action="store_true",
                   help="Poll until at least one engine is healthy (for CI/CD)")
    p.add_argument("--timeout", type=int, default=120,
                   help="Maximum wait seconds (default: 120, --wait only)")
    p.add_argument("--interval", type=int, default=5,
                   help="Poll interval in seconds (default: 5, --wait only)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=run)

    hsub = p.add_subparsers(dest="health_cmd")

    hist = hsub.add_parser("history", help="Show recent health check history from event bus")
    hist.add_argument("-n", "--last", type=int, default=10, help="Number of snapshots to show")
    hist.set_defaults(func=run_history)

    snap = hsub.add_parser("snapshot", help="Run health check and record result to event bus")
    snap.set_defaults(func=run_snapshot)

    trends = hsub.add_parser("trends", help="Show health score trend over recent snapshots")
    trends.add_argument("-n", "--last", type=int, default=20, help="Number of samples")
    trends.set_defaults(func=run_trends)


def run_wait(args: argparse.Namespace) -> int:
    """Poll engine endpoints until healthy or timeout."""
    import time as _time
    import json as _json
    timeout = getattr(args, "timeout", 120)
    interval = max(1, getattr(args, "interval", 5))
    store = StateStore(getattr(args, "state_dir", None))
    from aictl.core.config import load_config
    deadline = _time.monotonic() + timeout
    elapsed = 0

    while _time.monotonic() < deadline:
        config = load_config(store.dir)
        engines = config.engines.to_dict()
        healthy = []
        for name, url in engines.items():
            host = url.replace("http://", "").replace("https://", "").split(":")[0]
            port_s = url.replace("http://", "").replace("https://", "").split(":")[-1].split("/")[0]
            try:
                port = int(port_s)
                with socket.create_connection((host, port), timeout=2):
                    pass
                healthy.append(name)
            except Exception:
                pass

        if healthy:
            if getattr(args, "json", False):
                print_json({"healthy": True, "engines": healthy, "elapsed_s": round(elapsed, 1)})
            else:
                print(f"  ✓ Healthy ({', '.join(healthy)}) after {elapsed:.0f}s")
            return 0

        elapsed = _time.monotonic() - (deadline - timeout)
        if not getattr(args, "json", False):
            print(f"  Waiting for engines... {elapsed:.0f}s / {timeout}s")
        _time.sleep(interval)

    if getattr(args, "json", False):
        print_json({"healthy": False, "engines": [], "elapsed_s": timeout,
                    "error": f"No engine became healthy within {timeout}s"})
    else:
        print(f"  ✗ Timeout: no engine healthy after {timeout}s")
    return 1


def run(args: argparse.Namespace) -> int:
    """Execute the health command."""
    if getattr(args, "wait", False):
        return run_wait(args)
    store = StateStore(getattr(args, "state_dir", None))
    results: list[dict[str, Any]] = []
    score = 0
    total = 0

    def check(name: str, passed: bool, detail: str = "") -> None:
        """Check."""
        nonlocal score, total
        total += 1
        if passed:
            score += 1
        results.append({"check": name, "passed": passed, "detail": detail})

    # 1. Node initialized
    check("Node initialized", store.is_initialized())

    # 2. Hardware detection
    report = full_detect()
    check("CPU detected", report.system.cpu_cores > 0,
          f"{report.system.cpu_cores} cores")
    check("RAM adequate", report.system.ram_total_mb >= 4096,
          f"{report.system.ram_total_mb} MB")
    check("Disk space", report.system.disk_free_gb > 5,
          f"{report.system.disk_free_gb:.1f} GB free")

    # 3. Security
    from aictl.core.security import scan
    sec = scan(store.dir)
    check("Security score >= 50", sec.score >= 50, f"{sec.score}/100")

    # 4. Fabric
    from aictl.runtime.fabric import detect_memory_fabric
    fabric = detect_memory_fabric()
    check("Memory tiers detected", len(fabric.tiers) > 0,
          f"{len(fabric.tiers)} tiers, {fabric.total_capacity_gb:.0f} GB")

    # 5. Container runtime
    check("Container runtime", report.container_runtime != "none",
          report.container_runtime or "none")

    # 6. Engine reachability
    from aictl.core.config import load_config
    config = load_config(store.dir)
    engines = config.engines.to_dict()
    for name, url in engines.items():
        host = url.replace("http://", "").replace("https://", "").split(":")[0]
        port_s = url.replace("http://", "").replace("https://", "").split(":")[-1].split("/")[0]
        try:
            port = int(port_s)
            sock = socket.create_connection((host, port), timeout=2)
            sock.close()
            check(f"Engine: {name}", True, url)
        except Exception:
            check(f"Engine: {name}", False, f"{url} unreachable")

    # 7. Daemon
    try:
        import urllib.request
        import json
        with urllib.request.urlopen(f"http://{DAEMON_HOST}:{DAEMON_PORT}/v1/health", timeout=2) as r:
            data = json.loads(r.read())
        check("Daemon (aiosd)", data.get("status") == "ok", "port 7700")
    except Exception:
        check("Daemon (aiosd)", False, "not running")

    # 8. Recipes available
    from aictl.stack.manifest import list_recipes
    recipes = list_recipes()
    check("Recipes loaded", len(recipes) >= 8, f"{len(recipes)} recipes")

    # 9. Model recommendations
    from aictl.runtime.recommend import recommend
    recs = recommend(ram_mb=report.system.ram_total_mb, max_results=1)
    check("Model recommendations", len(recs) > 0)

    # Output
    if getattr(args, "json", False):
        print_json({"score": score, "total": total, "pct": round(score/total*100),
                    "checks": results})
        return 0

    pct = round(score / total * 100)
    icon = "\u2713" if pct >= 80 else ("\u26a0" if pct >= 50 else "\u2717")
    print(f"\n  {icon} Health: {score}/{total} ({pct}%)\n")

    for r in results:
        icon = "\u2713" if r["passed"] else "\u2717"
        detail = f" — {r['detail']}" if r["detail"] else ""
        print(f"  {icon} {r['check']}{detail}")

    print()
    return 0 if pct >= 50 else 1


def run_snapshot(args: argparse.Namespace) -> int:
    """Run health check and persist result to the event bus."""
    from aictl.core.events import emit as emit_event
    import time as _time

    # Re-use run() result but capture it
    store = StateStore(getattr(args, "state_dir", None))
    results: list[dict[str, Any]] = []
    score = 0
    total = 0

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal score, total
        total += 1
        if passed:
            score += 1
        results.append({"check": name, "passed": passed, "detail": detail})

    check("Node initialized", store.is_initialized())
    report = full_detect()
    check("CPU detected", report.system.cpu_cores > 0)
    check("RAM adequate", report.system.ram_total_mb >= 4096)
    check("Disk space", report.system.disk_free_gb > 5)

    pct = round(score / max(total, 1) * 100)
    emit_event("health.snapshot", source="aictl-health",
               score=score, total=total, pct=pct, ts=_time.time())

    if getattr(args, "json", False):
        print_json({"snapshot_recorded": True, "score": score, "total": total, "pct": pct})
        return 0

    from aictl.core.output import ok
    ok(f"Health snapshot recorded: {score}/{total} ({pct}%)")
    return 0


def run_history(args: argparse.Namespace) -> int:
    """Show recent health snapshots from the event bus."""
    import time as _time
    from aictl.core.events import get_bus

    bus = get_bus()
    n = getattr(args, "last", 10)
    events = [e for e in bus.recent(n=500)
              if getattr(e, "type", "") == "health.snapshot"]
    events = events[-n:]

    if getattr(args, "json", False):
        print_json([{"ts": e.timestamp, "score": e.data.get("score", 0),
                     "total": e.data.get("total", 0),
                     "pct": e.data.get("pct", 0)} for e in events])
        return 0

    if not events:
        print("No health snapshots recorded. Run: aictl health snapshot")
        return 0

    from aictl.core.output import print_table
    rows = [{"time": _time.strftime("%H:%M:%S", _time.localtime(e.timestamp)),
             "score": f"{e.data.get('score', 0)}/{e.data.get('total', 0)}",
             "pct": f"{e.data.get('pct', 0)}%",
             "status": "✓" if e.data.get("pct", 0) >= 80 else ("⚠" if e.data.get("pct", 0) >= 50 else "✗")}
            for e in events]
    print_table(rows, ["time", "score", "pct", "status"])
    return 0


def run_trends(args: argparse.Namespace) -> int:
    """Show health score trend as a simple ASCII sparkline."""
    from aictl.core.events import get_bus

    bus = get_bus()
    n = getattr(args, "last", 20)
    events = [e for e in bus.recent(n=500)
              if getattr(e, "type", "") == "health.snapshot"]
    events = events[-n:]

    if not events:
        print("No health snapshots available. Run: aictl health snapshot")
        return 0

    scores = [e.data.get("pct", 0) for e in events]
    avg = sum(scores) / len(scores)
    trend = "↑" if scores[-1] > scores[0] else ("↓" if scores[-1] < scores[0] else "→")

    if getattr(args, "json", False):
        print_json({"samples": len(scores), "avg_pct": round(avg, 1),
                    "min_pct": min(scores), "max_pct": max(scores),
                    "trend": trend, "scores": scores})
        return 0

    # Simple ASCII bar for each score
    print(f"\n  Health trend (last {len(scores)} snapshots)  avg={avg:.0f}%  {trend}")
    print()
    for i, s in enumerate(scores):
        bar = "█" * (s // 10)
        print(f"  [{i+1:2d}] {bar:<10} {s:3d}%")
    print()
    return 0
