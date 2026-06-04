"""aictl recommend — suggest models for your hardware."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json, print_table
from aictl.runtime.broker import full_detect
from aictl.runtime.recommend import recommend


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("recommend", help="Recommend models for your hardware")
    p.add_argument("--use-case", default="", choices=["chat", "code", "embedding", "vision", "stt", ""],
                   help="Filter by use case")
    p.add_argument("--top", type=int, default=5, help="Max results")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the recommend command."""
    report = full_detect()
    vram = sum(g.vram_mb for g in report.gpus)
    ram = report.system.ram_total_mb

    recs = recommend(
        vram_mb=vram, ram_mb=ram,
        use_case=getattr(args, "use_case", ""),
        max_results=getattr(args, "top", 5),
    )

    if getattr(args, "json", False):
        print_json([{"name": r.name, "runtime": r.runtime, "vram_mb": r.vram_required_mb,
                     "use_case": r.use_case, "notes": r.notes} for r in recs])
        return 0

    if not recs:
        print("No models fit your hardware. Consider adding a GPU.")
        return 0

    mode = "GPU" if vram > 0 else "CPU-only"
    vram_str = f"{vram // 1024}GB VRAM" if vram > 0 else "no GPU"
    ok(f"Recommended models ({mode}, {vram_str}, {ram // 1024}GB RAM)")
    print()
    rows = [{"name": r.name, "runtime": r.runtime,
             "vram": f"{r.vram_required_mb}MB", "use": r.use_case,
             "notes": r.notes} for r in recs]
    print_table(rows, ["name", "runtime", "vram", "use", "notes"])
    return 0
