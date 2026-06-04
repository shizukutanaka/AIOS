"""bootc image builder: generate disk images from the OS container.

Uses bootc-image-builder to create installable disk images:
  - qcow2 (KVM/QEMU)
  - raw (dd to disk)
  - vmdk (VMware)
  - iso (bare-metal install)
  - ami (AWS)
  - vhd (Azure)

Requires: podman + the bootc-image-builder container image.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


BUILDER_IMAGE = "quay.io/centos-bootc/bootc-image-builder:latest"

SUPPORTED_FORMATS = ("qcow2", "raw", "vmdk", "iso", "ami", "vhd")


@dataclass
class BuildResult:
    success: bool = False
    output_path: str = ""
    format: str = ""
    size_bytes: int = 0
    duration_sec: float = 0.0
    error: str = ""


def build_disk_image(
    container_image: str,
    output_dir: str = "./output",
    format: str = "qcow2",
    rootfs: str = "ext4",
    timeout: int = 1800,
) -> BuildResult:
    """Build a disk image from a bootc container image.

    Args:
        container_image: Source bootc container image (e.g. ghcr.io/org/aios:latest)
        output_dir: Directory for output files
        format: Output format (qcow2, raw, vmdk, iso, ami, vhd)
        rootfs: Root filesystem type (ext4, xfs, btrfs)
    """
    result = BuildResult(format=format)

    if format not in SUPPORTED_FORMATS:
        result.error = f"Unsupported format: {format}. Use: {', '.join(SUPPORTED_FORMATS)}"
        return result

    if not _has_podman():
        result.error = "podman required for disk image building"
        return result

    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        "sudo", "podman", "run",
        "--rm",
        "--privileged",
        "--pull=newer",
        "--security-opt", "label=type:unconfined_t",
        "-v", f"{os.path.abspath(output_dir)}:/output",
        "-v", "/var/lib/containers/storage:/var/lib/containers/storage",
        BUILDER_IMAGE,
        "--type", format,
        "--rootfs", rootfs,
        container_image,
    ]

    import time
    t0 = time.monotonic()

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        result.duration_sec = time.monotonic() - t0

        if proc.returncode == 0:
            result.success = True
            # Find output file
            for f in Path(output_dir).rglob(f"*.{format}"):
                result.output_path = str(f)
                result.size_bytes = f.stat().st_size
                break
            if not result.output_path:
                # Check common output patterns
                for ext in (format, "img", "raw"):
                    for f in Path(output_dir).rglob(f"*.{ext}"):
                        result.output_path = str(f)
                        result.size_bytes = f.stat().st_size
                        break
        else:
            result.error = proc.stderr.strip()[:500]

    except subprocess.TimeoutExpired:
        result.error = f"Build timed out after {timeout}s"
    except FileNotFoundError:
        result.error = "podman not found"

    return result


def list_formats() -> list[dict[str, str]]:
    """List supported output formats with descriptions."""
    return [
        {"format": "qcow2", "description": "KVM/QEMU virtual disk", "use": "libvirt, Proxmox"},
        {"format": "raw", "description": "Raw disk image", "use": "dd to physical disk"},
        {"format": "vmdk", "description": "VMware virtual disk", "use": "VMware ESXi/Workstation"},
        {"format": "iso", "description": "Bootable ISO installer", "use": "Bare-metal install"},
        {"format": "ami", "description": "Amazon Machine Image", "use": "AWS EC2"},
        {"format": "vhd", "description": "Azure virtual hard disk", "use": "Azure VMs"},
    ]


def _has_podman() -> bool:
    """Execute has podman."""
    import shutil
    return shutil.which("podman") is not None
