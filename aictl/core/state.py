"""State management for single-node aios.

State directory: ~/.aios/
  state.json   — node metadata, profile, init timestamp
  stacks.json  — applied stacks
  models.db    — SQLite for model registry + trust chain
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_STATE_DIR = Path.home() / ".aios"


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


class StateStore:
    """Filesystem + SQLite state store."""

    def __init__(self, state_dir: Path | None = None):
        """Initialize state store with directory path."""
        self.dir = state_dir or DEFAULT_STATE_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self.dir / "state.json"
        self._stacks_path = self.dir / "stacks.json"
        self._db_path = self.dir / "models.db"

    # ── node state ──────────────────────────────────────
    def is_initialized(self) -> bool:
        """Is initialized."""
        return self._state_path.exists()

    def save_node(self, ns: NodeState) -> None:
        """Save node."""
        self._state_path.write_text(json.dumps(asdict(ns), indent=2))

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
        self._stacks_path.write_text(
            json.dumps([asdict(e) for e in entries], indent=2)
        )

    def load_stacks(self) -> list[StackEntry]:
        """Load stacks."""
        if not self._stacks_path.exists():
            return []
        try:
            data = json.loads(self._stacks_path.read_text())
            return [StackEntry(**d) for d in data]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []  # graceful fallback on corrupted stacks file

    def upsert_stack(self, entry: StackEntry) -> None:
        """Upsert stack."""
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
