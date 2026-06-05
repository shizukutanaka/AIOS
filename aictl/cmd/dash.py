"""aictl dash — everything in one screen.

Apple principle: you should never need to run five commands to understand
what's happening. This command gives you the full picture in one view.

  aictl dash          One-shot snapshot
  aictl dash --watch  Auto-refresh every 5 seconds
"""

from __future__ import annotations

from typing import Any

import argparse

import os
import sys
import time


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "dash",
        help="Full system dashboard in one screen.",
    )
    p.add_argument(
        "--watch", "-w",
        action="store_true",
        help="Auto-refresh every 5 seconds (Ctrl-C to stop).",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Refresh interval in seconds (default: 5).",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return an exit code."""
    if args.watch:
        try:
            while True:
                _clear()
                _render()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n  Stopped.")
            return 0
    else:
        _render()
    return 0


def _clear() -> None:
    """Clear terminal (works on Linux/macOS/Windows)."""
    os.system("clear" if sys.platform != "win32" else "cls")


def _render() -> None:
    """Render the full dashboard."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    width = 56
    title = "aictl dashboard"
    pad = width - len(title) - len(now) - 4
    print()
    print(f"  ╔{'═' * width}╗")
    print(f"  ║  {title}{' ' * max(pad, 2)}{now}  ║")
    print(f"  ╚{'═' * width}╝")
    print()

    _section_system()
    _section_engines()
    _section_cache()
    _section_cost()
    _section_perf()
    _section_guard()
    _section_rag()


def _section_system() -> None:
    """Return formatted system section for the dashboard."""
    print("  ── System ─────────────────────────────────────────────")
    try:
        from aictl.runtime.broker import full_detect
        from aictl.metrics.slo import read_psi
        hw = full_detect()
        psi = read_psi()

        gpu_str = (
            f"{len(hw.gpus)} GPU, "
            f"{sum(g.vram_mb for g in hw.gpus)/1024:.0f}GB VRAM"
            if hw.gpus else "CPU only"
        )
        ram_gb = hw.system.ram_total_mb / 1024
        mem_psi = psi.memory_some_avg10
        mem_icon = "✓" if mem_psi < 25 else "⚠"

        print(f"  {gpu_str:<30}  RAM: {ram_gb:.0f}GB")
        print(f"  Profile: {hw.profile:<20}  "
              f"{mem_icon} Memory pressure: {mem_psi:.1f}%")
    except Exception as e:
        print(f"  (unavailable: {e})")
    print()


def _section_engines() -> None:
    """Return formatted engines section for the dashboard."""
    print("  ── Engines ────────────────────────────────────────────")
    try:
        from aictl.runtime.adapters import discover_engines
        engines = discover_engines()
        if not engines:
            print("  No engines configured.")
        else:
            for e in engines:
                icon = "✓" if e.reachable else "✗"
                models_str = f"  {len(e.models)} models" if e.models else ""
                latency = f"  {e.latency_ms:.0f}ms" if e.reachable else ""
                print(f"  {icon} {e.engine:<10} {e.endpoint:<30}{latency}{models_str}")
    except Exception as e:
        print(f"  (unavailable: {e})")
    print()


def _section_cache() -> None:
    """Return formatted semantic cache section for the dashboard."""
    print("  ── Semantic Cache ─────────────────────────────────────")
    try:
        from aictl.core.sem_cache import get_default_cache
        stats = get_default_cache().stats()
        hr = stats["session_hit_rate"] * 100
        icon = "✓" if hr >= 20 else "○"
        print(f"  {icon} Hit rate: {hr:.1f}%   "
              f"Entries: {stats['entries']:,}   "
              f"Tokens saved: {stats['total_tokens_saved']:,}")
    except Exception as e:
        print(f"  (unavailable: {e})")
    print()


def _section_cost() -> None:
    """Return formatted cost section for the dashboard."""
    print("  ── Cost (this session) ────────────────────────────────")
    try:
        from aictl.core.perf import read_recent
        records = read_recent(limit=200)
        if not records:
            print("  No activity yet.")
        else:
            # Rough cost from perf records (we don't store cost in perf yet)
            total_calls = len(records)
            failed = sum(1 for r in records if r.exit_code != 0)
            avg_ms = sum(r.duration_ms for r in records) / total_calls
            print(f"  Commands run:  {total_calls}   "
                  f"Failed: {failed}   "
                  f"Avg latency: {avg_ms:.0f}ms")
    except Exception as e:
        print(f"  (unavailable: {e})")
    print()


def _section_perf() -> None:
    """Return formatted performance section for the dashboard."""
    print("  ── Performance (top 5 slowest commands) ───────────────")
    try:
        from aictl.core.perf import summary
        summ = summary()
        if not summ:
            print("  No data yet.")
        else:
            items = sorted(summ.items(), key=lambda x: -x[1]["p95_ms"])[:5]
            for cmd, stats in items:
                bar_len = min(20, int(stats["p95_ms"] / 100))
                bar = "█" * bar_len
                print(f"  {cmd:<18} p95={stats['p95_ms']:>6.0f}ms  {bar}")
    except Exception as e:
        print(f"  (unavailable: {e})")
    print()


def _section_guard() -> None:
    """Return formatted guardrails section for the dashboard."""
    print("  ── Guardrails ─────────────────────────────────────────")
    try:
        # Check if guard has been used (look for guard in perf records)
        from aictl.core.perf import summary
        summ = summary()
        guard_stats = summ.get("guard", {})
        if guard_stats:
            print(f"  Scans: {guard_stats['count']}   "
                  f"Failures: {guard_stats['failures']}")
        else:
            print("  Not yet used this session.")
            print("  Try: aictl guard scan 'your text here'")
    except Exception as e:
        print(f"  (unavailable: {e})")
    print()


def _section_rag() -> None:
    """Return formatted RAG section for the dashboard."""
    print("  ── RAG Index ──────────────────────────────────────────")
    try:
        from aictl.core.rag import RagStore
        stats = RagStore().stats()
        if stats["documents"] == 0:
            print("  Empty. Try: aictl rag index ./docs")
        else:
            print(f"  {stats['documents']} docs   "
                  f"{stats['chunks']} chunks   "
                  f"{stats['db_size_mb']:.1f}MB")
    except Exception as e:
        print(f"  (unavailable: {e})")
    print()
