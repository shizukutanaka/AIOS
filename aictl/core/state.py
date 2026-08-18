"""State management for single-node aios.

State directory: ~/.aios/
  state.json   — node metadata, profile, init timestamp
  stacks.json  — applied stacks
  models.db    — SQLite for model registry + trust chain
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aictl.core.constants import STATE_DIR_PERMISSIONS
from typing import Any

from aictl.core.atomicio import atomic_write_text
from aictl.core.filelock import file_lock


DEFAULT_STATE_DIR = Path.home() / ".aios"

# The environment variable that moves the state directory. `AICTL_STATE_DIR` is
# an accepted alias: both names were already in use across this codebase, so
# neither could be deleted without breaking whatever was setting it.
STATE_DIR_ENV = "AIOS_STATE_DIR"
STATE_DIR_ENV_ALIAS = "AICTL_STATE_DIR"


def resolve_state_dir(explicit: "str | Path | None" = None) -> Path:
    """Where state lives, decided in exactly one place.

    It was previously decided in fifteen. Twelve modules read `AIOS_STATE_DIR`,
    two read `AICTL_STATE_DIR`, and `StateStore` — which owns `state.json`,
    `models.db`, the audit log and the API keys — read neither, because
    `DEFAULT_STATE_DIR` was a module constant evaluated at import. Setting the
    variable therefore *split* the state: `perf.jsonl` moved, `state.json` did
    not, and nothing said so.

    That also made a printed remedy false. `core/errors.py` answers a
    PermissionError with "run with AIOS_STATE_DIR=/tmp/aios", which did not
    move the file whose permissions were the problem.

    Precedence is explicit argument, then the canonical variable, then the
    alias, then `~/.aios`. The argument must win: the global `--state-dir` flag
    is the user being specific right now, and an inherited environment variable
    outranking it would make the flag silently useless.
    """
    if explicit:
        return Path(explicit).expanduser()
    for name in (STATE_DIR_ENV, STATE_DIR_ENV_ALIAS):
        value = os.environ.get(name)
        # An empty value means unset, not "the current directory" — the latter
        # would scatter state across whatever tree the user happened to be in.
        if value and value.strip():
            return Path(value).expanduser()
    return Path.home() / ".aios"


@dataclass
class NodeState:
    node_id: str = ""
    hostname: str = ""
    initialized_at: float = 0.0
    profile: str = ""  # e.g. "nvidia-rtx4090", "amd-mi300x", "cpu-only"
    version: str = "0.1.0"
    mode: str = "local"  # local | cluster
    gpu_count: int = 0
    vram_total_mb: int = 0
    ram_total_mb: int = 0


@dataclass
class StackEntry:
    name: str
    file: str
    applied_at: float = 0.0
    status: str = "pending"  # pending | running | stopped | error
    services: list[dict[str, Any]] = field(default_factory=list)


def _secure_state_dir(path: Path) -> bool:
    """Restrict the state directory to its owner. Returns True if changed.

    This directory holds cloud API keys (config.json), the metering ledger,
    the audit log, and every document indexed into RAG. It was being created
    with the process umask — 0755 on a typical system — leaving all of that
    world-readable.

    aictl already knew better in three places and acted in none: the constant
    STATE_DIR_PERMISSIONS declared 0700, `core/security.py` flagged the loose
    mode as a HIGH finding, and the remediation it printed was the very chmod
    performed here. Detecting a problem you can fix, and then only printing
    advice, is the least useful arrangement of those parts.

    Tightening an existing directory follows ssh's convention: a tool holding
    credentials is expected to insist on owner-only access rather than warn
    about it forever. It only ever removes access, never grants it, and is
    skipped when we do not own the directory — narrowing someone else's
    permissions is not this function's business.
    """
    try:
        info = path.stat()
        if (info.st_mode & 0o077) == 0:
            return False                      # already owner-only
        if info.st_uid != os.getuid():
            return False                      # not ours to re-permission
        path.chmod(STATE_DIR_PERMISSIONS)
        return True
    except (OSError, AttributeError):
        # Windows has no st_uid/getuid, and a read-only mount is a legitimate
        # deployment. Failing to tighten must never stop aictl from running.
        return False


class StateStore:
    """Filesystem + SQLite state store."""

    def __init__(self, state_dir: Path | str | None = None):
        """Initialize state store with directory path.

        Accepts either a Path or a str: the global `--state-dir` flag delivers a
        string, and ~24 commands pass it straight through as
        `StateStore(args.state_dir)`. Coerce to Path here so every caller works
        uniformly instead of crashing on `str.mkdir`.
        """
        self.dir = resolve_state_dir(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        _secure_state_dir(self.dir)
        self._state_path = self.dir / "state.json"
        self._stacks_path = self.dir / "stacks.json"
        self._db_path = self.dir / "models.db"

    # ── node state ──────────────────────────────────────
    def is_initialized(self) -> bool:
        """Is initialized."""
        return self._state_path.exists()

    def save_node(self, ns: NodeState) -> None:
        """Save node."""
        atomic_write_text(self._state_path, json.dumps(asdict(ns), indent=2))

    def load_node(self) -> NodeState:
        """Load node."""
        if not self._state_path.exists():
            return NodeState()
        try:
            d = json.loads(self._state_path.read_text())
            return NodeState(**{k: v for k, v in d.items() if k in NodeState.__dataclass_fields__})
        except (json.JSONDecodeError, KeyError, TypeError):
            return NodeState()  # graceful fallback on corrupted state file

    # ── stacks ──────────────────────────────────────────
    def save_stacks(self, entries: list[StackEntry]) -> None:
        """Save stacks."""
        atomic_write_text(
            self._stacks_path, json.dumps([asdict(e) for e in entries], indent=2)
        )

    def load_stacks(self) -> list[StackEntry]:
        """Load stacks."""
        if not self._stacks_path.exists():
            return []
        try:
            data = json.loads(self._stacks_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []  # graceful fallback on a corrupted stacks file
        # Filter unknown keys (forward-compat with newer schemas) and skip any
        # individual malformed entry — one bad row must not drop every stack.
        # Raw StackEntry(**d) would raise TypeError on an unknown field and the
        # whole list would be lost.
        entries: list[StackEntry] = []
        for d in data if isinstance(data, list) else []:
            if not isinstance(d, dict):
                continue
            try:
                entries.append(StackEntry(**{
                    k: v for k, v in d.items() if k in StackEntry.__dataclass_fields__
                }))
            except (TypeError, ValueError):
                continue  # skip just this entry
        return entries

    def upsert_stack(self, entry: StackEntry) -> None:
        """Upsert stack."""
        # Serialize load→modify→save so concurrent apply/down of different stacks
        # don't clobber each other in the shared stacks.json (lost-update race).
        with file_lock(self._stacks_path):
            stacks = self.load_stacks()
            for i, s in enumerate(stacks):
                if s.name == entry.name:
                    stacks[i] = entry
                    self.save_stacks(stacks)
                    return
            stacks.append(entry)
            self.save_stacks(stacks)

    def remove_stack(self, name: str) -> bool:
        """Remove stack."""
        with file_lock(self._stacks_path):
            stacks = self.load_stacks()
            new = [s for s in stacks if s.name != name]
            if len(new) == len(stacks):
                return False
            self.save_stacks(new)
            return True

    # ── model DB (SQLite) ───────────────────────────────
    def _db(self) -> sqlite3.Connection:
        """Execute db."""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS models (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                digest TEXT NOT NULL,
                size_bytes INTEGER,
                format TEXT,
                signed INTEGER DEFAULT 0,
                signer TEXT,
                registered_at REAL,
                status TEXT DEFAULT 'available'
            )"""
        )
        conn.commit()
        return conn

    def register_model(self, model_id: str, name: str, digest: str,
                       size_bytes: int = 0, fmt: str = "gguf",
                       signed: bool = False, signer: str = "",
                       registered_at: float = 0.0,
                       status: str = "available") -> None:
        """Register model. registered_at<=0 → now (preserves order on restore)."""
        db = self._db()
        try:
            db.execute(
                "INSERT OR REPLACE INTO models VALUES (?,?,?,?,?,?,?,?,?)",
                (model_id, name, digest, size_bytes, fmt, int(signed), signer,
                 registered_at if registered_at > 0 else time.time(), status),
            )
            db.commit()
        finally:
            db.close()

    def list_models(self) -> list[dict[str, Any]]:
        """List models."""
        db = self._db()
        try:
            cur = db.execute("SELECT * FROM models ORDER BY registered_at DESC")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            db.close()
