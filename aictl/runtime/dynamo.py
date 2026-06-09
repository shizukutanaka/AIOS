"""NVIDIA Dynamo integration: KVBM, NIXL, ModelExpress, Planner.

NVIDIA Dynamo (v0.8+, April 2026) is the "inference OS for AI factories".
This module provides integration points for aictl to work with Dynamo components:

  KVBM (KV Block Manager):
    4-tier memory hierarchy: GPU HBM → CPU DRAM → Local SSD → Remote Storage
    Blocks deduplicated by sequence hash, immutable once registered.
    Write-through: GPU → CPU → disk automatically.

  NIXL (Inference Transfer Library):
    Point-to-point data transfer across heterogeneous memory/storage.
    Supports NVLink, InfiniBand, RoCE, Ethernet, GPUDirect Storage.
    Used for KV cache transfer in disaggregated prefill/decode.

  ModelExpress:
    Streams model weights GPU-to-GPU via NIXL/NVLink.
    7x faster cold-start for new replicas.

  Planner:
    SLA-driven autoscaler. Profiles workloads, right-sizes GPU pools.

  Grove:
    K8s operator for topology-aware gang scheduling (NVL72).

  AIConfigurator:
    Simulates 10K+ deployment configs in seconds.
    Finds optimal serving config without burning GPU-hours.

  DGDR (Zero-config deploy):
    model + hardware + SLA → auto-profile → auto-topology → deploy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class KVBMConfig:
    """KVBM 4-tier memory configuration."""
    # Tier capacities (GB)
    gpu_hbm_gb: float = 0.0       # G1: GPU HBM (fastest, most expensive)
    cpu_dram_gb: float = 0.0      # G2: CPU DRAM (fast, large)
    local_ssd_gb: float = 0.0     # G3: Local/pooled SSD
    remote_storage_gb: float = 0.0  # G4: NFS/object store (slowest, largest)

    # Block configuration
    block_size_tokens: int = 16    # Tokens per block (vLLM default: 16)
    eviction_policy: str = "lru"   # lru | priority | retention

    # NIXL transport
    nixl_backend: str = "tcp"      # tcp | rdma | gds | nvlink
    nixl_enable_gds: bool = False  # GPUDirect Storage


@dataclass
class DGDRSpec:
    """Zero-config deployment spec (Dynamo-style DGDR).

    Specify model + hardware + SLA → system auto-configures everything.
    """
    model: str                     # e.g. "meta-llama/Llama-3.2-8B-Instruct"
    hardware: str = "auto"         # auto | H100 | A100 | RTX4090
    sla_ttft_ms: int = 500         # Target TTFT p95
    sla_tpot_ms: int = 30          # Target Time Per Output Token p95
    sla_throughput_tps: int = 100  # Target throughput (tokens/sec)
    max_gpus: int = 8              # Max GPUs to allocate
    quantization: str = "auto"     # auto | fp16 | fp8 | int8 | int4
    disagg: bool = False           # Enable prefill/decode disaggregation
    max_cost_per_hour: float = 0.0  # Cost constraint (0 = unlimited)


def detect_dynamo() -> dict[str, Any]:
    """Detect if NVIDIA Dynamo components are available."""
    result: dict[str, Any] = {
        "dynamo_available": False,
        "kvbm_available": False,
        "nixl_available": False,
        "grove_available": False,
        "version": "",
    }

    # Check for Dynamo binary
    import shutil
    if shutil.which("dynamo"):
        result["dynamo_available"] = True
        try:
            import subprocess
            ver = subprocess.check_output(["dynamo", "--version"],
                                          timeout=5, text=True).strip()
            result["version"] = ver
        except Exception:
            pass  # best-effort; failure is non-critical

    # Check for NIXL library
    nixl_paths = [
        Path("/usr/lib/libnixl.so"),
        Path("/usr/local/lib/libnixl.so"),
        Path("/opt/nvidia/dynamo/lib/libnixl.so"),
    ]
    result["nixl_available"] = any(p.exists() for p in nixl_paths)

    # Check for Grove K8s operator
    if shutil.which("kubectl"):
        try:
            import subprocess
            out = subprocess.check_output(
                ["kubectl", "get", "crd", "gangjobs.grove.nvidia.com"],
                timeout=5, text=True, stderr=subprocess.DEVNULL,
            )
            if "gangjobs" in out:
                result["grove_available"] = True
        except Exception:
            pass  # best-effort; failure is non-critical

    return result


def generate_kvbm_config(fabric_report: Any = None) -> KVBMConfig:
    """Generate KVBM config based on detected memory fabric."""
    config = KVBMConfig()

    if fabric_report is None:
        from aictl.runtime.fabric import detect_memory_fabric
        fabric_report = detect_memory_fabric()

    for tier in fabric_report.tiers:
        if tier.name == "dram":
            config.cpu_dram_gb = tier.available_gb * 0.5  # Reserve 50% for KV cache
        elif tier.name == "nvme":
            config.local_ssd_gb = tier.available_gb * 0.3  # 30% for KV offload
        elif tier.name == "cxl":
            config.cpu_dram_gb += tier.capacity_gb  # CXL extends DRAM tier

    # Check for RDMA/GDS
    if Path("/dev/infiniband").exists():
        config.nixl_backend = "rdma"
    if Path("/dev/nvidia-fs0").exists():
        config.nixl_enable_gds = True
        config.nixl_backend = "gds"

    return config


def generate_dgdr_yaml(spec: DGDRSpec) -> dict[str, Any]:
    """Generate a DGDR-style zero-config deployment manifest.

    This is the Dynamo way: specify WHAT you want, not HOW to deploy.
    """
    manifest = {
        "apiVersion": "dynamo.nvidia.com/v1",
        "kind": "InferenceDeployment",
        "metadata": {
            "name": spec.model.split("/")[-1].lower().replace(".", "-"),
            "labels": {"aios.managed": "true"},
        },
        "spec": {
            "model": spec.model,
            "hardware": spec.hardware,
            "sla": {
                "ttft_p95_ms": spec.sla_ttft_ms,
                "tpot_p95_ms": spec.sla_tpot_ms,
                "throughput_tps": spec.sla_throughput_tps,
            },
            "constraints": {
                "maxGPUs": spec.max_gpus,
                "quantization": spec.quantization,
            },
            "features": {
                "disaggregation": spec.disagg,
                "kvbm": {"enabled": True, "evictionPolicy": "lru"},
                "modelExpress": {"enabled": True},  # 7x faster cold-start
            },
        },
    }

    if spec.max_cost_per_hour > 0:
        manifest["spec"]["constraints"]["maxCostPerHour"] = spec.max_cost_per_hour

    return manifest


def estimate_dgdr_resources(spec: DGDRSpec) -> dict[str, Any]:
    """Estimate resources needed for a DGDR spec (like AIConfigurator)."""
    # Rough model size estimation
    model_lower = spec.model.lower()

    # Parameter estimation from model name
    params_b = 0
    for s in ["405b", "70b", "32b", "27b", "14b", "8b", "7b", "3b", "1b"]:
        if s in model_lower:
            params_b = float(s.rstrip("b"))
            break

    if params_b == 0:
        params_b = 7  # Default assumption

    # VRAM estimation
    bytes_per_param = {"fp16": 2, "fp8": 1, "int8": 1, "int4": 0.5, "auto": 1.5}
    bpp = bytes_per_param.get(spec.quantization, 2)
    model_vram_gb = params_b * bpp
    kv_overhead_gb = model_vram_gb * 0.2  # ~20% for KV cache
    total_vram_gb = model_vram_gb + kv_overhead_gb

    # GPU count
    vram_per_gpu = {"H100": 80, "H200": 141, "A100": 80, "RTX4090": 24, "auto": 80}
    gpu_vram = vram_per_gpu.get(spec.hardware, 80)
    # total_vram_gb is a float, so the integer-ceil idiom (a+b-1)//b is invalid
    # (// floors and under-counts fractional remainders) — use math.ceil.
    gpus_needed = max(1, math.ceil(total_vram_gb / gpu_vram))
    gpus_needed = min(gpus_needed, spec.max_gpus)

    # Throughput estimation
    tps_per_gpu = {"H100": 280, "H200": 450, "A100": 130, "RTX4090": 80, "auto": 200}
    est_tps = tps_per_gpu.get(spec.hardware, 200) * gpus_needed

    return {
        "model_params_b": params_b,
        "model_vram_gb": round(model_vram_gb, 1),
        "total_vram_gb": round(total_vram_gb, 1),
        "gpus_needed": gpus_needed,
        "gpu_type": spec.hardware if spec.hardware != "auto" else "H100",
        "estimated_tps": est_tps,
        "meets_sla": est_tps >= spec.sla_throughput_tps,
        "disagg_recommended": params_b >= 70 or spec.sla_ttft_ms < 200,
    }
