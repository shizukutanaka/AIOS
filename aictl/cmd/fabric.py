"""aictl fabric — memory fabric detection and placement policy."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, warn, print_json, print_kv, print_table
from aictl.runtime.broker import full_detect
from aictl.runtime.fabric import detect_memory_fabric, generate_placement_policy, generate_damon_config
from aictl.metrics.slo import read_psi


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("fabric", help="Memory fabric detection and placement")
    fsub = p.add_subparsers(dest="fabric_cmd")

    detect = fsub.add_parser("detect", help="Detect memory tiers")
    detect.set_defaults(func=run_detect)

    policy = fsub.add_parser("policy", help="Generate placement policy")
    policy.set_defaults(func=run_policy)

    migrate = fsub.add_parser("migrate", help="Show DAMON-based migration hints for a model")
    migrate.add_argument("model", help="Model name")
    migrate.add_argument("--pid", type=int, default=0, help="Process PID (0=auto-detect)")
    migrate.set_defaults(func=run_migrate)

    monitor = fsub.add_parser("monitor", help="Show current memory pressure (PSI) status")
    monitor.set_defaults(func=run_monitor)

    damon = fsub.add_parser("damon", help="Generate DAMON monitoring config for a PID")
    damon.add_argument("pid", type=int, help="Process PID to monitor")
    damon.add_argument("--sample-us", type=int, default=5000,
                       help="DAMON sample interval in microseconds")
    damon.set_defaults(func=run_damon)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_detect(args: argparse.Namespace) -> int:
    """Execute the detect subcommand."""

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


def run_migrate(args: argparse.Namespace) -> int:
    """Show DAMON-based migration hints for a running model process."""
    report = detect_memory_fabric()
    pid = getattr(args, "pid", 0)

    if not report.damon_available:
        warn("DAMON not available on this kernel — migration hints are advisory only")

    policy = generate_placement_policy(report, vram_gb=0)
    hints = [
        {"data": "model_weights", "current": "dram", "recommended": policy.model_weights,
         "action": f"mmap/MADV_SEQUENTIAL — keep in {policy.model_weights}"},
        {"data": "kv_cache", "current": "dram", "recommended": policy.kv_cache,
         "action": f"MADV_WILLNEED hot pages → {policy.kv_cache}"},
        {"data": "kv_cache_overflow", "current": "nvme", "recommended": policy.kv_cache_overflow,
         "action": f"Cold pages → MADV_PAGEOUT → {policy.kv_cache_overflow}"},
        {"data": "rag_cache", "current": "dram", "recommended": policy.rag_cache,
         "action": f"Embeddings → {policy.rag_cache} via numactl"},
    ]

    if getattr(args, "json", False):
        print_json({
            "model": args.model,
            "pid": pid,
            "damon_available": report.damon_available,
            "hints": hints,
        })
        return 0

    ok(f"Migration hints for: {args.model}")
    if pid:
        print(f"  PID: {pid}")
    print_table(hints, ["data", "current", "recommended", "action"])
    if report.damon_available and pid:
        print(f"\n  Enable monitoring: aictl fabric damon {pid}")
    return 0


def run_monitor(args: argparse.Namespace) -> int:
    """Show current memory pressure (PSI) and tier utilization."""
    report = detect_memory_fabric()
    psi = read_psi()

    pressure = {
        "memory_some_avg10": psi.memory_some_avg10,
        "memory_some_avg60": psi.memory_some_avg60,
        "cpu_some_avg10": psi.cpu_some_avg10,
        "io_some_avg10": psi.io_some_avg10,
    }

    tier_summary = [{"tier": t.name, "capacity_gb": round(t.capacity_gb, 1),
                     "available_gb": round(t.available_gb, 1),
                     "used_pct": round((1 - t.available_gb / max(t.capacity_gb, 1)) * 100, 1)}
                    for t in report.tiers]

    if getattr(args, "json", False):
        print_json({"pressure": pressure, "tiers": tier_summary,
                    "damon": report.damon_available})
        return 0

    ok("Memory Fabric Pressure Monitor")
    print()
    print("  PSI (Pressure Stall Information):")
    print_kv([
        ("memory avg10", f"{psi.memory_some_avg10:.1f}%"),
        ("memory avg60", f"{psi.memory_some_avg60:.1f}%"),
        ("cpu avg10",    f"{psi.cpu_some_avg10:.1f}%"),
        ("io avg10",     f"{psi.io_some_avg10:.1f}%"),
    ], indent=4)

    if tier_summary:
        print()
        print("  Memory tiers:")
        print_table(tier_summary, ["tier", "capacity_gb", "available_gb", "used_pct"])

    if psi.memory_some_avg10 > 25:
        warn(f"High memory pressure ({psi.memory_some_avg10:.0f}%) — consider migrating cold data to CXL/NVMe")
    return 0


def run_damon(args: argparse.Namespace) -> int:
    """Generate DAMON monitoring configuration for a process PID."""
    config = generate_damon_config(
        pid=args.pid,
        sample_us=getattr(args, "sample_us", 5000),
    )

    if getattr(args, "json", False):
        print_json(config)
        return 0

    ok(f"DAMON config for PID {args.pid}")
    print(f"\n  {config['description']}")
    print()
    print("  sysfs writes:")
    for path, value in config["sysfs_writes"].items():
        print(f"    echo {value!r} > {path}")
    print()
    print("  Notes:")
    for note in config["notes"]:
        print(f"    • {note}")
    return 0
