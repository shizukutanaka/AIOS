"""Context snapshots: save and restore service state across upgrades.

When upgrading the OS or restarting services, context snapshots preserve:
  - Running stack configurations
  - Model loading state
  - Active endpoint mappings
  - SLO configuration
  - Cluster topology

This enables "upgrade without losing context" from the spec.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aictl.core.state import StateStore


@dataclass
class Snapshot:
    snapshot_id: str
    created_at: float
    version: str
    node_state: dict[str, Any] = field(default_factory=dict)
    stacks: list[dict[str, Any]] = field(default_factory=list)
    cluster: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    models: list[dict[str, Any]] = field(default_factory=list)


class SnapshotManager:
    """Manage context snapshots for safe upgrades."""

    def __init__(self, store: StateStore):
        """Initialize snapshot manager."""
        self.store = store
        self.snap_dir = store.dir / "snapshots"
        self.snap_dir.mkdir(parents=True, exist_ok=True)

    def create(self, label: str = "") -> Snapshot:
        """Create a full state snapshot."""
        import uuid
        snap_id = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
        if label:
            snap_id = f"{label}_{snap_id}"

        node = self.store.load_node()
        stacks = self.store.load_stacks()
        models = self.store.list_models()

        # Load cluster state if exists
        cluster_path = self.store.dir / "cluster.json"
        cluster = {}
        if cluster_path.exists():
            try:
                cluster = json.loads(cluster_path.read_text())
            except json.JSONDecodeError:
                pass  # best-effort; failure is non-critical

        # Load config if exists
        config_path = self.store.dir / "config.json"
        config = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
            except json.JSONDecodeError:
                pass  # best-effort; failure is non-critical

        snap = Snapshot(
            snapshot_id=snap_id,
            created_at=time.time(),
            version=node.version,
            node_state=asdict(node),
            stacks=[asdict(s) for s in stacks],
            cluster=cluster,
            config=config,
            models=models,
        )

        # Write snapshot
        snap_path = self.snap_dir / f"{snap_id}.json"
        snap_path.write_text(json.dumps(asdict(snap), indent=2, default=str))

        return snap

    def list_snapshots(self) -> list[dict[str, Any]]:
        """List all available snapshots."""
        snaps: list[dict[str, Any]] = []
        for f in sorted(self.snap_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                snaps.append({
                    "id": data.get("snapshot_id", f.stem),
                    "created_at": data.get("created_at", 0),
                    "version": data.get("version", ""),
                    "stacks": len(data.get("stacks", [])),
                    "models": len(data.get("models", [])),
                    "size_bytes": f.stat().st_size,
                })
            except (json.JSONDecodeError, OSError):
                pass  # best-effort; failure is non-critical
        return snaps

    def restore(self, snapshot_id: str) -> tuple[bool, str]:
        """Restore state from a snapshot."""
        snap_path = self._find_snapshot(snapshot_id)
        if not snap_path:
            return False, f"Snapshot not found: {snapshot_id}"

        try:
            data = json.loads(snap_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            return False, f"Failed to read snapshot: {e}"

        # Restore node state
        from aictl.core.state import NodeState, StackEntry
        if "node_state" in data:
            ns_data = data["node_state"]
            ns = NodeState(**{k: v for k, v in ns_data.items() if k in NodeState.__dataclass_fields__})
            self.store.save_node(ns)

        # Restore stacks
        if "stacks" in data:
            entries = []
            for s in data["stacks"]:
                entries.append(StackEntry(**{k: v for k, v in s.items() if k in StackEntry.__dataclass_fields__}))
            self.store.save_stacks(entries)

        # Restore cluster
        if "cluster" in data and data["cluster"]:
            cluster_path = self.store.dir / "cluster.json"
            cluster_path.write_text(json.dumps(data["cluster"], indent=2))

        # Restore config
        if "config" in data and data["config"]:
            config_path = self.store.dir / "config.json"
            config_path.write_text(json.dumps(data["config"], indent=2))

        # Restore models
        if "models" in data:
            for m in data["models"]:
                self.store.register_model(
                    model_id=m.get("id", ""),
                    name=m.get("name", ""),
                    digest=m.get("digest", ""),
                    size_bytes=int(m.get("size_bytes", 0) or 0),
                    fmt=m.get("format", "gguf"),
                    signed=bool(m.get("signed", 0)),
                    signer=m.get("signer", ""),
                    registered_at=float(m.get("registered_at", 0) or 0),
                    status=m.get("status", "available"),
                )

        return True, f"Restored from snapshot: {snapshot_id}"

    def delete(self, snapshot_id: str) -> bool:
        """Delete."""
        snap_path = self._find_snapshot(snapshot_id)
        if snap_path:
            snap_path.unlink()
            return True
        return False

    def _find_snapshot(self, snapshot_id: str) -> Path | None:
        """Find snapshot file by ID (exact or prefix match)."""
        exact = self.snap_dir / f"{snapshot_id}.json"
        if exact.exists():
            return exact
        # Prefix match
        for f in self.snap_dir.glob("*.json"):
            if f.stem.startswith(snapshot_id):
                return f
        return None
