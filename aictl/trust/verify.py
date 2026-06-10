"""Minimal trust chain: digest verification for model bundles.

v1 scope: SHA-256 digest match. Future: cosign signature verification.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: str | Path, chunk_size: int = 65536) -> str:
    """Sha256 file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def verify_digest(path: str | Path, expected: str) -> bool:
    """Verify file digest matches expected value."""
    if not expected:
        return True  # no policy
    actual = sha256_file(path)
    return actual == expected


class TrustPolicy:
    """Trust policy for model loading."""

    def __init__(self, mode: str = "warn"):
        """Initialize model verifier."""
        self.mode = mode  # enforce | warn | disabled

    def check(self, path: str | Path, expected_digest: str) -> tuple[bool, str]:
        """Check."""
        if self.mode == "disabled":
            return True, ""

        if not expected_digest:
            if self.mode == "enforce":
                return False, "No digest specified — enforce mode rejects unsigned models"
            return True, "WARNING: no digest — skipping verification"

        try:
            match = verify_digest(path, expected_digest)
        except FileNotFoundError:
            if self.mode == "enforce":
                return False, f"File not found: {path}"
            return True, f"WARNING: file not found, skipping digest check: {path}"

        if match:
            return True, "Digest verified"

        if self.mode == "enforce":
            return False, "Digest mismatch — model rejected"
        return True, "WARNING: digest mismatch"
