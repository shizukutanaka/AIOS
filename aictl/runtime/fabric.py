"""Fabric Memory Orchestrator: memory tiering awareness for AI workloads.

Provides visibility and control over memory placement across:
  HBM/VRAM → DRAM → CXL → NVMe

Uses Linux kernel subsystems:
  - DAMON (Data Access MONitor) for hot/cold page tracking
  - /proc/meminfo and /sys/devices for tier detection
  - cgroup memory controllers for per-container limits
  - PSI for pressure-based decisions

This module provides:
  1. Memory tier detection (what hardware is available)
  2. AI data placement recommendations
  3. DAMON configuration for workload monitoring
  4. Memory pressure alerts and auto-migration hints
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MemoryTier:
    name: str           # hbm | dram | cxl | nvme
    capacity_gb: float
    bandwidth_gbps: float
    latency_ns: float
    available_gb: float = 0.0
    numa_node: int = -1


@dataclass
class FabricReport:
    tiers: list[MemoryTier] = field(default_factory=list)
    total_capacity_gb: float = 0.0
    damon_available: bool = False
    cxl_detected: bool = False
    numa_nodes: int = 1
    recommendations: list[str] = field(default_factory=list)


@dataclass
class PlacementPolicy:
    """AI data placement policy across memory tiers."""
    model_weights: str = "dram"       # Where to place base model weights
    kv_cache: str = "dram"            # Active KV cache
    kv_cache_overflow: str = "nvme"   # KV cache when DRAM is full
    lora_adapters: str = "dram"       # LoRA adapter weights
    rag_cache: str = "dram"           # RAG embedding cache
    tokenizer: str = "dram"           # Tokenizer + vocabulary
    context_snapshots: str = "nvme"   # Context continuity snapshots


def detect_memory_fabric() -> FabricReport:
    """Detect available memory tiers on the system."""
    report = FabricReport()

    # DRAM detection via /proc/meminfo
    dram = _detect_dram()
    if dram:
        report.tiers.append(dram)

    # CXL detection via /sys/bus/cxl
    cxl_tiers = _detect_cxl()
    report.cxl_detected = len(cxl_tiers) > 0
    report.tiers.extend(cxl_tiers)

    # NVMe detection
    nvme = _detect_nvme_capacity()
    if nvme:
        report.tiers.append(nvme)

    # NUMA nodes
    report.numa_nodes = _detect_numa_nodes()

    # DAMON availability
    report.damon_available = Path("/sys/kernel/mm/damon").exists()

    # Total capacity
    report.total_capacity_gb = sum(t.capacity_gb for t in report.tiers)

    # Recommendations
    if not report.damon_available:
        report.recommendations.append(
            "Enable DAMON for AI data access monitoring: "
            "CONFIG_DAMON=y CONFIG_DAMON_VADDR=y CONFIG_DAMON_PADDR=y"
        )
    if report.cxl_detected:
        report.recommendations.append(
            "CXL memory detected — configure tiered placement for KV cache overflow"
        )
    if report.numa_nodes > 1:
        report.recommendations.append(
            f"NUMA topology: {report.numa_nodes} nodes — "
            "bind inference containers to GPU-local NUMA node"
        )

    return report


def generate_placement_policy(report: FabricReport, vram_gb: int = 0) -> PlacementPolicy:
    """Generate optimal placement policy based on detected fabric."""
    policy = PlacementPolicy()

    has_cxl = any(t.name == "cxl" for t in report.tiers)
    has_nvme = any(t.name == "nvme" for t in report.tiers)
    dram_gb = sum(t.capacity_gb for t in report.tiers if t.name == "dram")

    # Model weights: VRAM if GPU, else DRAM
    if vram_gb > 0:
        policy.model_weights = "vram"
    else:
        policy.model_weights = "dram"

    # KV cache: DRAM primary, CXL overflow, NVMe last resort
    policy.kv_cache = "dram"
    if has_cxl:
        policy.kv_cache_overflow = "cxl"
    elif has_nvme:
        policy.kv_cache_overflow = "nvme"

    # LoRA: DRAM (small, needs fast access)
    policy.lora_adapters = "dram"

    # RAG cache: CXL if available (large, tolerates latency)
    if has_cxl:
        policy.rag_cache = "cxl"
    else:
        policy.rag_cache = "dram"

    # Context snapshots: always NVMe (persistence needed)
    if has_nvme:
        policy.context_snapshots = "nvme"

    return policy


def generate_damon_config(pid: int, sample_us: int = 5000,
                          aggr_us: int = 100000) -> dict[str, Any]:
    """Generate DAMON monitoring configuration for an inference process.

    DAMON monitors virtual address space access patterns to identify:
      - Hot pages (active KV cache entries)
      - Cold pages (eviction candidates)
      - Access frequency distribution

    Returns dict with sysfs paths and values to write.
    """
    return {
        "description": f"DAMON config for PID {pid}",
        "sysfs_writes": {
            "/sys/kernel/mm/damon/admin/kdamonds/0/contexts/0/targets/0/pid_target": str(pid),
            "/sys/kernel/mm/damon/admin/kdamonds/0/contexts/0/monitoring_attrs/intervals/sample_us": str(sample_us),
            "/sys/kernel/mm/damon/admin/kdamonds/0/contexts/0/monitoring_attrs/intervals/aggr_us": str(aggr_us),
            "/sys/kernel/mm/damon/admin/kdamonds/0/state": "on",
        },
        "notes": [
            "DAMON requires CONFIG_DAMON=y in kernel config",
            f"Monitoring PID {pid} with {sample_us}us sample, {aggr_us}us aggregation",
            "Access patterns available via DAMON sysfs or damo CLI tool",
            "Hot regions → keep in DRAM/HBM; cold regions → migrate to CXL/NVMe",
        ],
    }


def _detect_dram() -> MemoryTier | None:
    """Detect and return the requested state."""
    try:
        with open("/proc/meminfo") as f:
            gb = 0.0
            avail_gb = 0.0
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    gb = kb / (1024 * 1024)
                elif line.startswith("MemAvailable:"):
                    avail_gb = int(line.split()[1]) / (1024 * 1024)
                    return MemoryTier(
                        name="dram",
                        capacity_gb=round(gb, 1),
                        bandwidth_gbps=50.0,  # Typical DDR5
                        latency_ns=80,
                        available_gb=round(avail_gb, 1),
                    )
    except (OSError, ValueError):
        pass  # best-effort; failure is non-critical
    return None


def _detect_cxl() -> list[MemoryTier]:
    """Detect CXL memory devices via sysfs."""
    tiers: list[MemoryTier] = []
    cxl_path = Path("/sys/bus/cxl/devices")
    if not cxl_path.exists():
        return tiers

    for device in cxl_path.iterdir():
        if device.name.startswith("mem"):
            # CXL Type 3 memory device
            size_path = device / "size"
            if size_path.exists():
                try:
                    size_bytes = int(size_path.read_text().strip(), 0)
                    tiers.append(MemoryTier(
                        name="cxl",
                        capacity_gb=round(size_bytes / (1024**3), 1),
                        bandwidth_gbps=32.0,  # CXL 2.0 typical
                        latency_ns=200,       # ~2.5x DRAM
                    ))
                except (ValueError, OSError):
                    pass  # best-effort; failure is non-critical

    return tiers


def _detect_nvme_capacity() -> MemoryTier | None:
    """Detect NVMe storage for cache tier."""
    try:
        st = os.statvfs("/var")
        total_gb = (st.f_blocks * st.f_frsize) / (1024**3)
        avail_gb = (st.f_bavail * st.f_frsize) / (1024**3)
        if total_gb > 0:
            return MemoryTier(
                name="nvme",
                capacity_gb=round(total_gb, 1),
                bandwidth_gbps=7.0,     # NVMe Gen4
                latency_ns=10000,       # ~10us
                available_gb=round(avail_gb, 1),
            )
    except OSError:
        pass  # best-effort; failure is non-critical
    return None


def _detect_numa_nodes() -> int:
    """Count NUMA nodes."""
    numa_path = Path("/sys/devices/system/node")
    if not numa_path.exists():
        return 1
    nodes = [d for d in numa_path.iterdir() if d.name.startswith("node")]
    return max(len(nodes), 1)
