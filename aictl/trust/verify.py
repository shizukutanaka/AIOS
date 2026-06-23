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


def _canonical_digest(value: str) -> str:
    """Canonicalize a digest for comparison: lowercase, single 'sha256:' prefix.

    A SHA-256 digest is case-insensitive hex and is written variously as
    'sha256:<hex>', a bare '<hex>', or uppercased (depending on the registry/tool
    that emitted it). `sha256_file` always produces 'sha256:<lowercase hex>', so a
    strict `==` falsely rejected a digest that matched but was formatted
    differently — and in enforce mode that rejects a VALID model. Normalizing both
    sides fixes this WITHOUT weakening the check: the full 64-char hex body must
    still match exactly; only case and the optional algorithm prefix are folded.
    """
    s = value.strip().lower()
    for prefix in ("sha256:", "sha-256:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return f"sha256:{s}"


def verify_digest(path: str | Path, expected: str) -> bool:
    """Verify file digest matches expected value."""
    if not expected:
        return True  # no policy
    actual = sha256_file(path)
    return _canonical_digest(actual) == _canonical_digest(expected)


class TrustPolicy:
    """Trust policy for model loading."""

    VALID_MODES = frozenset({"enforce", "warn", "disabled"})

    def __init__(self, mode: str = "warn"):
        """Initialize model verifier."""
        if mode not in self.VALID_MODES:
            raise ValueError(
                f"Invalid TrustPolicy mode {mode!r}; "
                f"must be one of: {', '.join(sorted(self.VALID_MODES))}"
            )
        self.mode = mode

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
