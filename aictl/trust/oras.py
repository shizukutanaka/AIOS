"""ORAS model distribution: pull and push AI models as OCI artifacts.

Uses the `oras` CLI to interact with OCI-compliant registries:
  - Pull model weights from registry
  - Push model bundles with metadata
  - Attach SBOM and signatures as referrers
  - List available models in a registry

This enables "model supply chain" — signed, versioned, auditable model distribution.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelArtifact:
    reference: str           # registry/repo:tag
    digest: str = ""
    media_type: str = ""
    size_bytes: int = 0
    annotations: dict[str, str] = field(default_factory=dict)


@dataclass
class PullResult:
    success: bool = False
    local_path: str = ""
    digest: str = ""
    size_bytes: int = 0
    error: str = ""


def oras_available() -> bool:
    """Oras available."""
    return shutil.which("oras") is not None


def pull_model(
    reference: str,
    output_dir: str | Path = "",
    timeout: int = 600,
) -> PullResult:
    """Pull a model artifact from an OCI registry.

    Args:
        reference: OCI reference (e.g. ghcr.io/org/models/llama3:8b-q4)
        output_dir: Local directory to store the model
    """
    result = PullResult()

    if not oras_available():
        # Fallback to skopeo or podman
        return _pull_fallback(reference, output_dir, timeout)

    if not output_dir:
        output_dir = Path.home() / ".aios" / "models"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "oras", "pull", reference,
        "--output", str(output_dir),
    ]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            result.success = True
            result.local_path = str(output_dir)
            # Parse digest from output
            for line in r.stdout.splitlines():
                if "Digest:" in line:
                    result.digest = line.split("Digest:")[-1].strip()
                elif "Downloaded" in line:
                    pass  # Could parse size
        else:
            result.error = r.stderr.strip()[:200]
    except subprocess.TimeoutExpired:
        result.error = "Pull timed out"
    except FileNotFoundError:
        result.error = "oras binary not found"

    return result


def push_model(
    reference: str,
    files: list[str | Path],
    annotations: dict[str, str] | None = None,
    timeout: int = 600,
) -> tuple[bool, str]:
    """Push model files as an OCI artifact.

    Args:
        reference: Target OCI reference
        files: List of file paths to push
        annotations: Key-value annotations (e.g. model name, format, license)
    """
    if not oras_available():
        return False, "oras not installed"

    cmd = ["oras", "push", reference]

    # Add file arguments with media types
    for f in files:
        p = Path(f)
        if p.suffix in (".gguf", ".bin"):
            cmd.append(f"{p}:application/vnd.aios.model.weights.v1")
        elif p.suffix in (".safetensors",):
            cmd.append(f"{p}:application/vnd.aios.model.safetensors.v1")
        elif p.suffix in (".json",):
            cmd.append(f"{p}:application/vnd.aios.model.config.v1+json")
        else:
            cmd.append(str(f))

    if annotations:
        for k, v in annotations.items():
            cmd.extend(["--annotation", f"{k}={v}"])

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, r.stderr.strip()[:200]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return False, str(e)


def attach_sbom(
    reference: str,
    sbom_path: str | Path,
    sbom_format: str = "spdx+json",
    timeout: int = 60,
) -> tuple[bool, str]:
    """Attach an SBOM to a model artifact as a referrer."""
    if not oras_available():
        return False, "oras not installed"

    media_type = {
        "spdx+json": "application/spdx+json",
        "cyclonedx+json": "application/vnd.cyclonedx+json",
    }.get(sbom_format, f"application/{sbom_format}")

    cmd = [
        "oras", "attach", reference,
        "--artifact-type", media_type,
        str(sbom_path),
    ]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return True, "SBOM attached"
        return False, r.stderr.strip()[:200]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return False, str(e)


def list_referrers(reference: str, timeout: int = 30) -> list[ModelArtifact]:
    """List referrers (SBOMs, signatures) attached to an artifact."""
    if not oras_available():
        return []

    cmd = ["oras", "discover", reference, "--output", "json"]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return []

        data = json.loads(r.stdout)
        results: list[ModelArtifact] = []
        for ref in data.get("referrers", []):
            results.append(ModelArtifact(
                reference=reference,
                digest=ref.get("digest", ""),
                media_type=ref.get("artifactType", ref.get("mediaType", "")),
                size_bytes=ref.get("size", 0),
                annotations=ref.get("annotations", {}),
            ))
        return results
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return []


def _pull_fallback(reference: str, output_dir: str | Path, timeout: int) -> PullResult:
    """Fallback pull using skopeo or podman."""
    result = PullResult()

    for tool in ("skopeo", "podman"):
        if not shutil.which(tool):
            continue

        if not output_dir:
            output_dir = Path.home() / ".aios" / "models"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if tool == "skopeo":
            cmd = ["skopeo", "copy", f"docker://{reference}", f"dir:{output_dir}"]
        else:
            cmd = ["podman", "pull", reference]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                result.success = True
                result.local_path = str(output_dir)
                return result
            result.error = r.stderr.strip()[:200]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    if not result.error:
        result.error = "No pull tool available (install oras, skopeo, or podman)"
    return result
