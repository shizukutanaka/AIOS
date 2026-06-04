"""aictl fabric — memory fabric detection and placement policy."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json, print_kv, print_table
from aictl.runtime.broker import full_detect


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("fabric", help="Memory fabric detection and placement")
    fsub = p.add_subparsers(dest="fabric_cmd")

    detect = fsub.add_parser("detect", help="Detect memory tiers")
    detect.set_defaults(func=run_detect)

    policy = fsub.add_parser("policy", help="Generate placement policy")
    policy.set_defaults(func=run_policy)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_detect(args: argparse.Namespace) -> int:
    """Execute the detect subcommand."""
    from aictl.runtime.fabric import detect_memory_fabric

    report = detect_memory_fabric()

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json(asdict(report))
        return 0

    ok(f"Memory Fabric ({report.total_capacity_gb:.1f} GB across {len(report.tiers)} tiers)")
    print()

    rows = [{"tier": t.name.upper(), "capacity": f"{t.capacity_gb:.1f} GB",
             "available": f"{t.available_gb:.1f} GB",
             "bandwidth": f"{t.bandwidth_gbps:.0f} GB/s",
             "latency": f"{t.latency_ns:.0f} ns"} for t in report.tiers]
    print_table(rows, ["tier", "capacity", "available", "bandwidth", "latency"])

    print_kv([
        ("DAMON", "available" if report.damon_available else "not available"),
        ("CXL", "detected" if report.cxl_detected else "not detected"),
        ("NUMA", f"{report.numa_nodes} nodes"),
    ], indent=2)

    if report.recommendations:
        print("\n  Recommendations:")
        for r in report.recommendations:
            print(f"    → {r}")
    return 0


def run_policy(args: argparse.Namespace) -> int:
    """Execute the policy subcommand."""
    from aictl.runtime.fabric import detect_memory_fabric, generate_placement_policy

    hw = full_detect()
    vram = sum(g.vram_mb for g in hw.gpus) // 1024

    report = detect_memory_fabric()
    policy = generate_placement_policy(report, vram_gb=vram)

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json(asdict(policy))
        return 0

    ok("AI Data Placement Policy")
    print()
    print_kv([
        ("Model weights", policy.model_weights),
        ("KV cache", policy.kv_cache),
        ("KV overflow", policy.kv_cache_overflow),
        ("LoRA adapters", policy.lora_adapters),
        ("RAG cache", policy.rag_cache),
        ("Tokenizer", policy.tokenizer),
        ("Snapshots", policy.context_snapshots),
    ], indent=2)
    return 0
