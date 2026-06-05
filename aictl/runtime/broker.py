"""Runtime Broker: GPU/NPU/CPU detection and profile selection.

Design: Rob Pike's simplicity — detect what's there, pick the best profile,
expose it as a simple data structure. No over-abstraction.

Profiles are strings like:
  nvidia-<arch>-<vram>   e.g. nvidia-ada-24gb
  amd-<arch>-<vram>      e.g. amd-rdna3-16gb
  intel-npu-<gen>        e.g. intel-npu-mtl
  cpu-only
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass
class GPUInfo:
    index: int
    name: str
    vendor: str          # nvidia | amd | intel
    vram_mb: int
    driver_version: str
    compute_cap: str     # e.g. "8.9" for NVIDIA, "gfx1100" for AMD
    mig_capable: bool = False
    mig_enabled: bool = False


@dataclass
class NPUInfo:
    name: str
    vendor: str          # intel | amd
    driver_loaded: bool
    runtime: str         # openvino | xdna


@dataclass
class SystemInfo:
    hostname: str = ""
    cpu_model: str = ""
    cpu_cores: int = 0
    ram_total_mb: int = 0
    swap_total_mb: int = 0
    disk_free_gb: float = 0.0
    kernel: str = ""
    cgroup_v2: bool = False
    psi_enabled: bool = False


@dataclass
class RuntimeReport:
    system: SystemInfo = field(default_factory=SystemInfo)
    gpus: list[GPUInfo] = field(default_factory=list)
    npus: list[NPUInfo] = field(default_factory=list)
    profile: str = "cpu-only"
    container_runtime: str = ""  # podman | docker | none
    ollama_available: bool = False
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


def _run(cmd: list[str], timeout: int = 10) -> str | None:
    """Execute the command and return an exit code."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def detect_system() -> SystemInfo:
    """Detect system."""
    si = SystemInfo()
    si.hostname = os.uname().nodename
    si.kernel = os.uname().release

    # CPU
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    si.cpu_model = line.split(":", 1)[1].strip()
                    break
        si.cpu_cores = os.cpu_count() or 0
    except OSError:
        pass  # best-effort; failure is non-critical

    # RAM
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    si.ram_total_mb = int(line.split()[1]) // 1024
                elif line.startswith("SwapTotal"):
                    si.swap_total_mb = int(line.split()[1]) // 1024
    except OSError:
        pass  # best-effort; failure is non-critical

    # Disk
    try:
        st = os.statvfs("/")
        si.disk_free_gb = round(st.f_bavail * st.f_frsize / (1024**3), 1)
    except OSError:
        pass  # best-effort; failure is non-critical

    # cgroup v2
    si.cgroup_v2 = os.path.isfile("/sys/fs/cgroup/cgroup.controllers")

    # PSI
    si.psi_enabled = os.path.isfile("/proc/pressure/memory")

    return si


def detect_nvidia() -> list[GPUInfo]:
    """Detect NVIDIA GPUs via nvidia-smi."""
    if not shutil.which("nvidia-smi"):
        return []

    out = _run([
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,driver_version,compute_cap,mig.mode.current",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return []

    gpus: list[GPUInfo] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        mig_str = parts[5] if len(parts) > 5 else "Disabled"
        gpus.append(GPUInfo(
            index=int(parts[0]),
            name=parts[1],
            vendor="nvidia",
            vram_mb=int(float(parts[2])),
            driver_version=parts[3],
            compute_cap=parts[4],
            mig_capable="A100" in parts[1] or "H100" in parts[1] or "H200" in parts[1],
            mig_enabled=mig_str.lower() == "enabled",
        ))
    return gpus


def detect_amd() -> list[GPUInfo]:
    """Detect AMD GPUs via rocm-smi."""
    if not shutil.which("rocm-smi"):
        return []

    out = _run(["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--csv"])
    if not out:
        return []

    gpus: list[GPUInfo] = []
    # Fallback: parse lspci for AMD GPUs
    lspci = _run(["lspci", "-nn"])
    if lspci:
        idx = 0
        for line in lspci.splitlines():
            if "VGA" in line and ("AMD" in line or "ATI" in line):
                name_match = re.search(r"\[(.+?)\]", line)
                gpus.append(GPUInfo(
                    index=idx,
                    name=name_match.group(1) if name_match else "AMD GPU",
                    vendor="amd",
                    vram_mb=0,
                    driver_version="",
                    compute_cap="",
                ))
                idx += 1
    return gpus


def detect_npus() -> list[NPUInfo]:
    """Detect Intel/AMD NPUs."""
    npus: list[NPUInfo] = []

    # Intel NPU (accel device)
    if os.path.exists("/dev/accel/accel0") or os.path.exists("/dev/accel0"):
        npus.append(NPUInfo(
            name="Intel NPU",
            vendor="intel",
            driver_loaded=True,
            runtime="openvino",
        ))

    # AMD XDNA
    if os.path.exists("/dev/amdxdna") or os.path.exists("/dev/accel/accel1"):
        npus.append(NPUInfo(
            name="AMD XDNA NPU",
            vendor="amd",
            driver_loaded=True,
            runtime="xdna",
        ))

    # Huawei Ascend NPU (npu device, used by DeepSeek V4)
    for i in range(8):
        if os.path.exists(f"/dev/davinci{i}"):
            npus.append(NPUInfo(
                name=f"Huawei Ascend NPU {i}",
                vendor="huawei",
                driver_loaded=True,
                runtime="cann",
            ))
            break  # Count as one device class
    # Alternative: check for Ascend driver
    if not any(n.vendor == "huawei" for n in npus):
        if os.path.exists("/usr/local/Ascend") or shutil.which("npu-smi"):
            npus.append(NPUInfo(
                name="Huawei Ascend NPU",
                vendor="huawei",
                driver_loaded=True,
                runtime="cann",
            ))

    # Qualcomm Hexagon NPU
    if os.path.exists("/dev/qcom-nsp0"):
        npus.append(NPUInfo(
            name="Qualcomm Hexagon NPU",
            vendor="qualcomm",
            driver_loaded=True,
            runtime="qnn",
        ))

    return npus


def detect_container_runtime() -> str:
    """Detect container runtime."""
    if shutil.which("podman"):
        return "podman"
    if shutil.which("docker"):
        return "docker"
    return "none"


def detect_ollama() -> bool:
    """Detect ollama."""
    return shutil.which("ollama") is not None


def select_profile(gpus: list[GPUInfo], npus: list[NPUInfo]) -> str:
    """Select the best runtime profile based on detected hardware.

    Priority: NVIDIA GPU > AMD GPU > Intel NPU > AMD NPU > CPU-only
    """
    if not gpus and not npus:
        return "cpu-only"

    # Pick the best GPU
    best_gpu: GPUInfo | None = None
    for g in gpus:
        if best_gpu is None or g.vram_mb > best_gpu.vram_mb:
            best_gpu = g

    if best_gpu:
        vram_label = f"{best_gpu.vram_mb // 1024}gb" if best_gpu.vram_mb >= 1024 else f"{best_gpu.vram_mb}mb"
        arch = _infer_arch(best_gpu)
        return f"{best_gpu.vendor}-{arch}-{vram_label}"

    # NPU fallback
    if npus:
        npu = npus[0]
        return f"{npu.vendor}-npu-{npu.runtime}"

    return "cpu-only"


def _infer_arch(g: GPUInfo) -> str:
    """Execute infer arch."""
    name = g.name.lower()
    if g.vendor == "nvidia":
        if any(x in name for x in ["h100", "h200", "h800"]):
            return "hopper"
        if any(x in name for x in ["4090", "4080", "4070", "4060", "l40", "l4"]):
            return "ada"
        if any(x in name for x in ["a100", "a6000", "a5000", "a40"]):
            return "ampere"
        if any(x in name for x in ["3090", "3080", "3070", "3060"]):
            return "ampere"
        return "gpu"
    if g.vendor == "amd":
        if any(x in name for x in ["mi300", "mi250"]):
            return "cdna"
        if any(x in name for x in ["7900", "7800", "7700", "7600"]):
            return "rdna3"
        return "gpu"
    return "gpu"


def full_detect() -> RuntimeReport:
    """Run full hardware detection and return a RuntimeReport."""
    system = detect_system()
    gpus = detect_nvidia() + detect_amd()
    npus = detect_npus()
    profile = select_profile(gpus, npus)
    container = detect_container_runtime()
    ollama = detect_ollama()

    issues: list[str] = []
    recs: list[str] = []

    if not system.cgroup_v2:
        issues.append("cgroup v2 not detected — resource control limited")
    if not system.psi_enabled:
        issues.append("PSI not enabled — SLO pressure monitoring unavailable")
    if container == "none":
        issues.append("No container runtime (podman/docker) found")
        recs.append("Install podman: sudo dnf install podman")
    if not gpus and not npus:
        recs.append("No GPU/NPU detected — CPU-only mode. Consider adding a GPU for inference.")
    if gpus and container == "none":
        issues.append("GPU detected but no container runtime for GPU workloads")

    # NVIDIA-specific
    for g in gpus:
        if g.vendor == "nvidia" and g.mig_capable and not g.mig_enabled:
            recs.append(f"GPU {g.index} ({g.name}) supports MIG — enable for multi-tenant isolation")

    return RuntimeReport(
        system=system,
        gpus=gpus,
        npus=npus,
        profile=profile,
        container_runtime=container,
        ollama_available=ollama,
        issues=issues,
        recommendations=recs,
    )
