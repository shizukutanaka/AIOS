"""Quadlet generator: convert Stack services into systemd Quadlet units.

Quadlet is Podman's native systemd integration. Each service in a Stack
becomes a .container file under ~/.config/containers/systemd/ (rootless)
or /etc/containers/systemd/ (root).

This is the heart of "local-first orchestration without K8s".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aictl.stack.manifest import ServiceDef, StackManifest


QUADLET_DIR_ROOTLESS = Path.home() / ".config/containers/systemd"
QUADLET_DIR_ROOT = Path("/etc/containers/systemd")


@dataclass
class QuadletUnit:
    filename: str           # e.g. aios-local-chat-llm.container
    content: str            # Full unit file content
    service_name: str       # systemd service name (auto-derived)


def generate_quadlets(manifest: StackManifest, rootless: bool = True) -> list[QuadletUnit]:
    """Generate Quadlet .container files for all services in a Stack."""
    units: list[QuadletUnit] = []

    for svc in manifest.services:
        if svc.runtime == "ollama" and not svc.image:
            # Ollama runs natively, not as a container — skip Quadlet
            continue

        image = _resolve_image(svc)
        if not image:
            continue

        unit_name = f"aios-{manifest.name}-{svc.name}"
        filename = f"{unit_name}.container"

        content = _build_container_unit(svc, manifest, image, unit_name)
        units.append(QuadletUnit(
            filename=filename,
            content=content,
            service_name=f"{unit_name}.service",
        ))

    return units


def _resolve_image(svc: ServiceDef) -> str:
    """Resolve container image for a service."""
    if svc.image:
        return svc.image

    IMAGE_MAP = {
        "vllm": "vllm/vllm-openai:latest",
        "sglang": "lmsysorg/sglang:latest",
        "trt-llm": "nvcr.io/nvidia/tritonserver:latest",
        "ollama": "docker.io/ollama/ollama:latest",
    }
    return IMAGE_MAP.get(svc.runtime, "")


def _build_container_unit(svc: ServiceDef, manifest: StackManifest,
                          image: str, unit_name: str) -> str:
    """Build a Quadlet .container file."""
    lines: list[str] = []

    # [Unit]
    lines.append("[Unit]")
    lines.append(f"Description=AI OS — {manifest.name}/{svc.name}")

    # Dependencies between services in the same stack
    deps = [s for s in manifest.services if s.name != svc.name and s.runtime in ("ollama", "vllm", "sglang")]
    for dep in deps:
        dep_unit = f"aios-{manifest.name}-{dep.name}.service"
        if _is_backend(dep) and not _is_backend(svc):
            lines.append(f"After={dep_unit}")
            lines.append(f"Requires={dep_unit}")

    lines.append("")

    # [Container]
    lines.append("[Container]")
    lines.append(f"Image={image}")
    lines.append(f"ContainerName={unit_name}")

    # Port mapping
    if svc.port:
        lines.append(f"PublishPort={svc.port}:{svc.port}")

    # GPU passthrough
    if svc.gpu_required:
        lines.append("AddDevice=nvidia.com/gpu=all")
        lines.append("SecurityLabelDisable=true")

    # Shared memory for inference engines
    if svc.runtime in ("vllm", "sglang", "trt-llm"):
        lines.append("ShmSize=1g")

    # Environment variables
    for k, v in svc.env.items():
        lines.append(f"Environment={k}={v}")

    # Model as command argument
    if svc.runtime == "vllm" and svc.model:
        lines.append(f"Exec=--model {svc.model} --port {svc.port or 8000}")
    elif svc.runtime == "sglang" and svc.model:
        lines.append(f"Exec=--model-path {svc.model} --port {svc.port or 8000}")
    elif svc.runtime == "ollama":
        lines.append(f"Environment=OLLAMA_HOST=0.0.0.0:{svc.port or 11434}")

    # Health check
    if svc.health_path:
        port = svc.port or 8080
        lines.append(f"HealthCmd=curl -sf http://localhost:{port}{svc.health_path} || exit 1")
        lines.append("HealthInterval=30s")
        lines.append("HealthRetries=3")
        lines.append("HealthStartPeriod=60s")

    # Labels for management
    lines.append(f"Label=aios.stack={manifest.name}")
    lines.append(f"Label=aios.service={svc.name}")
    lines.append(f"Label=aios.runtime={svc.runtime}")

    # Auto-update
    lines.append("AutoUpdate=registry")

    lines.append("")

    # [Service]
    lines.append("[Service]")
    lines.append("Restart=on-failure")
    lines.append("RestartSec=10")
    lines.append("TimeoutStartSec=120")

    # Resource limits via systemd
    if svc.gpu_memory_mb > 0:
        # Memory limit = GPU VRAM + overhead
        mem_limit = svc.gpu_memory_mb + 2048
        lines.append(f"MemoryMax={mem_limit}M")

    lines.append("")

    # [Install]
    lines.append("[Install]")
    lines.append("WantedBy=multi-user.target default.target")

    return "\n".join(lines) + "\n"


def _is_backend(svc: ServiceDef) -> bool:
    """Return True if  backend."""
    return svc.runtime in ("ollama", "vllm", "sglang", "trt-llm")


def write_quadlets(units: list[QuadletUnit], rootless: bool = True,
                   dry_run: bool = False) -> list[Path]:
    """Write Quadlet unit files to the appropriate directory."""
    target_dir = QUADLET_DIR_ROOTLESS if rootless else QUADLET_DIR_ROOT
    written: list[Path] = []

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    for unit in units:
        path = target_dir / unit.filename
        if not dry_run:
            path.write_text(unit.content)
        written.append(path)

    return written


def remove_quadlets(stack_name: str, rootless: bool = True) -> list[Path]:
    """Remove Quadlet files for a stack."""
    target_dir = QUADLET_DIR_ROOTLESS if rootless else QUADLET_DIR_ROOT
    removed: list[Path] = []

    if not target_dir.exists():
        return removed

    prefix = f"aios-{stack_name}-"
    for f in target_dir.iterdir():
        if f.name.startswith(prefix) and f.suffix == ".container":
            f.unlink()
            removed.append(f)

    return removed


def reload_systemd() -> bool:
    """Reload systemd to pick up new/changed Quadlet units."""
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", "daemon-reload"],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def validate_quadlet(content: str) -> list[str]:
    """Validate a Quadlet .container file for common issues.

    Returns list of issues (empty = valid).
    """
    issues: list[str] = []
    lines = content.splitlines()

    sections = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            sections.add(stripped[1:-1])

    # Required sections
    if "Container" not in sections:
        issues.append("Missing [Container] section")
    if "Install" not in sections:
        issues.append("Missing [Install] section (service won't start on boot)")

    # Required fields
    has_image = any("Image=" in line for line in lines)
    if not has_image:
        issues.append("Missing Image= in [Container]")

    has_name = any("ContainerName=" in line for line in lines)
    if not has_name:
        issues.append("Missing ContainerName= (will use auto-generated name)")

    # GPU check
    any("AddDevice=" in line and "nvidia" in line.lower() for line in lines)
    any("NVIDIA_VISIBLE_DEVICES" in line for line in lines)

    # Health check recommendation
    has_health = any("HealthCmd=" in line for line in lines)
    if not has_health:
        issues.append("No HealthCmd= defined (recommended for production)")

    # Auto-update
    has_autoupdate = any("AutoUpdate=" in line for line in lines)
    if not has_autoupdate:
        issues.append("No AutoUpdate= defined (consider AutoUpdate=registry)")

    return issues
