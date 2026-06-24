"""Crash-safe file writes.

A plain ``path.write_text(...)`` truncates the target before writing the new
bytes, so a crash, full disk, or interrupt mid-write leaves a truncated, corrupt
state file — and aictl's state (config, tenants, quotas, node/stacks) is exactly
the kind of cumulative data that is painful to lose.

``atomic_write_text`` writes to a temp file in the *same directory*, flushes and
fsyncs it, then atomically replaces the target with ``os.replace`` (atomic on
POSIX and Windows when src/dst are on the same filesystem). On any failure the
original file is left untouched and the temp file is cleaned up.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Union


def atomic_write_text(path: Union[str, Path], text: str, encoding: str = "utf-8",
                      mode: int | None = None) -> None:
    """Atomically write ``text`` to ``path`` (crash-safe; leaves original on failure).

    ``mode`` (e.g. ``0o600``) sets the final file's permission bits explicitly.
    Secret files (API keys) must be owner-only — a plain ``write_text`` leaves the
    file at the process umask (commonly world-readable ``0o644``). ``mkstemp``
    already creates the temp file ``0o600``, but passing ``mode`` makes the intent
    explicit and independent of umask.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Temp file in the same directory guarantees os.replace stays on one
    # filesystem (cross-device rename is not atomic and would raise).
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
