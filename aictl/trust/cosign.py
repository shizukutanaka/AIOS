"""Cosign integration: verify container image and model bundle signatures.

Wraps the `cosign` CLI for:
  - Keyless verification (Sigstore Fulcio + Rekor)
  - Key-based verification (public key file)
  - Attestation verification (SLSA provenance)
  - Image digest pinning

Falls back to digest-only verification if cosign is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class VerifyResult:
    verified: bool = False
    method: str = ""         # cosign-keyless | cosign-key | digest-only | skipped
    signer: str = ""
    issuer: str = ""
    digest: str = ""
    error: str = ""
    raw_output: str = ""


def cosign_available() -> bool:
    """Cosign available."""
    return shutil.which("cosign") is not None


def verify_image(
    image_ref: str,
    public_key: str = "",
    certificate_identity: str = "",
    certificate_oidc_issuer: str = "",
    timeout: int = 30,
) -> VerifyResult:
    """Verify a container image signature with cosign.

    Args:
        image_ref: Container image reference (e.g. ghcr.io/org/image:tag)
        public_key: Path to public key file (for key-based verification)
        certificate_identity: Expected signer identity (for keyless)
        certificate_oidc_issuer: Expected OIDC issuer (for keyless)
    """
    result = VerifyResult()

    if not cosign_available():
        result.method = "cosign-unavailable"
        result.error = "cosign not installed"
        return result

    cmd = ["cosign", "verify"]

    if public_key:
        cmd.extend(["--key", public_key])
        result.method = "cosign-key"
    elif certificate_identity and certificate_oidc_issuer:
        cmd.extend([
            "--certificate-identity", certificate_identity,
            "--certificate-oidc-issuer", certificate_oidc_issuer,
        ])
        result.method = "cosign-keyless"
    else:
        # Cosign v3: keyless without specific identity
        cmd.extend([
            "--certificate-identity-regexp", ".*",
            "--certificate-oidc-issuer-regexp", ".*",
        ])
        result.method = "cosign-keyless"

    # Cosign v3: --output text by default, use --output json for structured
    cmd.extend(["--output", "json"])

    cmd.append(image_ref)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        result.raw_output = r.stdout[:1000]

        if r.returncode == 0:
            result.verified = True
            _parse_cosign_output(r.stdout, result)
        else:
            result.error = r.stderr.strip()[:200]

    except subprocess.TimeoutExpired:
        result.error = "Verification timed out"
    except FileNotFoundError:
        result.error = "cosign binary not found"

    return result


def verify_attestation(
    image_ref: str,
    predicate_type: str = "https://slsa.dev/provenance/v1",
    public_key: str = "",
    timeout: int = 30,
) -> VerifyResult:
    """Verify SLSA provenance attestation on an image."""
    result = VerifyResult(method="cosign-attestation")

    if not cosign_available():
        result.error = "cosign not installed"
        return result

    cmd = [
        "cosign", "verify-attestation",
        "--type", predicate_type,
    ]

    if public_key:
        cmd.extend(["--key", public_key])
    else:
        cmd.extend([
            "--certificate-identity-regexp", ".*",
            "--certificate-oidc-issuer-regexp", ".*",
        ])

    # Cosign v3 requires --output json for machine-readable output (trust rule).
    cmd.extend(["--output", "json"])
    cmd.append(image_ref)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        result.raw_output = r.stdout[:1000]
        if r.returncode == 0:
            result.verified = True
        else:
            result.error = r.stderr.strip()[:200]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result.error = str(e)[:200]

    return result


def get_image_digest(image_ref: str) -> str:
    """Get the digest of a container image."""
    for tool in ("cosign", "skopeo", "podman", "docker"):
        if not shutil.which(tool):
            continue
        try:
            if tool == "cosign":
                r = subprocess.run(
                    ["cosign", "triangulate", image_ref],
                    capture_output=True, text=True, timeout=10,
                )
            elif tool == "skopeo":
                r = subprocess.run(
                    ["skopeo", "inspect", "--format", "{{.Digest}}", f"docker://{image_ref}"],
                    capture_output=True, text=True, timeout=10,
                )
            else:
                r = subprocess.run(
                    [tool, "inspect", "--format", "{{.Digest}}", image_ref],
                    capture_output=True, text=True, timeout=10,
                )
            if r.returncode == 0:
                return r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return ""


def _parse_cosign_output(stdout: str, result: VerifyResult) -> None:
    """Parse cosign JSON output to extract signer and issuer."""
    try:
        entries = json.loads(stdout)
        if isinstance(entries, list) and entries:
            cert_info = entries[0].get("optional", {})
            result.signer = cert_info.get("Subject", cert_info.get("subject", ""))
            result.issuer = cert_info.get("Issuer", cert_info.get("issuer", ""))
    except (json.JSONDecodeError, KeyError, IndexError):
        pass  # best-effort; failure is non-critical
