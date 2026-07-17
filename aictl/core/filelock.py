"""Inter-process file locking for read-modify-write on shared state.

Atomic writes (atomicio) keep a single write from corrupting a file, but they do
not serialize a *read-modify-write* across processes: two concurrent
``aictl quota create`` runs each read the registry, add their own team, and write
the whole dict back — last writer wins, silently losing the other's update.

``file_lock`` wraps the entire load→modify→save in an exclusive advisory lock
(``fcntl.flock``) so concurrent writers run one at a time. On platforms without
fcntl it degrades to a no-op (best-effort) rather than failing.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - non-POSIX fallback
    _HAVE_FCNTL = False


@contextmanager
def file_lock(path: Union[str, Path]) -> Iterator[None]:
    """Hold an exclusive cross-process lock for the duration of the block.

    The lock uses a sibling ``<path>.lock`` file so it does not interfere with
    atomic replacement of ``path`` itself.
    """
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not _HAVE_FCNTL:
        yield  # best-effort: no locking available on this platform
        return
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
