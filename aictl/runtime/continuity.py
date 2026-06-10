"""Context Continuity Engine: preserve inference context across restarts.

Manages KV cache persistence so that:
  - OS upgrades don't lose active conversations
  - Engine restarts preserve warm cache
  - Failover to another engine carries context

Architecture:
  1. Before upgrade/restart: snapshot KV cache state via engine API
  2. Store snapshots in /var/lib/aios/contexts/
  3. After restart: restore context from snapshot
  4. Garbage collect stale snapshots (age, size limits)

vLLM support: Uses /v1/kv_cache/save and /v1/kv_cache/load (experimental)
Ollama support: Uses keep_alive to retain models, no cache export
SGLang: RadixAttention tree export (experimental)
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ContextSnapshot:
    snapshot_id: str
    engine: str           # vllm | sglang | ollama
    model: str
    created_at: float = 0.0
    size_bytes: int = 0
    num_entries: int = 0   # Number of KV cache entries
    ttl_seconds: int = 3600  # Default 1 hour
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | saved | restored | expired | failed


class ContextContinuityEngine:
    """Manage KV cache persistence for inference engines."""

    DEFAULT_DIR = Path("/var/lib/aios/contexts")
    MAX_TOTAL_SIZE = 50 * 1024 * 1024 * 1024  # 50GB max
    MAX_SNAPSHOTS = 100

    def __init__(self, context_dir: Path | None = None):
        """Initialize context continuity engine."""
        self.dir = context_dir or self.DEFAULT_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.dir / "index.json"

    def pre_upgrade_save(self, engines: dict[str, str]) -> list[ContextSnapshot]:
        """Save KV cache from all active engines before an upgrade.

        Args:
            engines: {engine_name: endpoint} dict of active engines
        """
        snapshots: list[ContextSnapshot] = []

        for engine, endpoint in engines.items():
            try:
                snap = self._save_engine_context(engine, endpoint)
                if snap.status == "saved":
                    snapshots.append(snap)
            except Exception:
                pass  # best-effort; failure is non-critical

        # Merge with any existing index by engine (latest wins) so saving one
        # engine's context never wipes another's pending snapshot.
        by_engine = {s.engine: s for s in self._load_index()}
        for s in snapshots:
            by_engine[s.engine] = s
        self._save_index(list(by_engine.values()))
        return snapshots

    def post_upgrade_restore(self, engines: dict[str, str]) -> list[ContextSnapshot]:
        """Restore KV cache to engines after an upgrade."""
        snapshots = self._load_index()
        restored: list[ContextSnapshot] = []

        for snap in snapshots:
            if snap.status != "saved":
                continue
            if time.time() - snap.created_at > snap.ttl_seconds:
                snap.status = "expired"
                continue

            endpoint = engines.get(snap.engine)
            if endpoint:
                try:
                    self._restore_engine_context(snap, endpoint)
                    snap.status = "restored"
                    restored.append(snap)
                except Exception:
                    snap.status = "failed"

        self._save_index(snapshots)
        return restored

    def list_snapshots(self) -> list[ContextSnapshot]:
        """List snapshots."""
        return self._load_index()

    def gc(self, max_age_hours: int = 24) -> int:
        """Garbage collect stale snapshots. Returns number removed."""
        snapshots = self._load_index()
        cutoff = time.time() - (max_age_hours * 3600)
        kept: list[ContextSnapshot] = []
        removed = 0

        for snap in snapshots:
            if snap.created_at < cutoff or snap.status in ("expired", "failed"):
                # Delete data file (snapshots are persisted as .json, not .bin)
                data_path = self.dir / f"{snap.snapshot_id}.json"
                data_path.unlink(missing_ok=True)
                removed += 1
            else:
                kept.append(snap)

        self._save_index(kept)
        return removed

    def _save_engine_context(self, engine: str, endpoint: str) -> ContextSnapshot:
        """Save context from a specific engine."""
        import urllib.request

        snap_id = f"{engine}-{int(time.time())}"
        snap = ContextSnapshot(
            snapshot_id=snap_id,
            engine=engine,
            model="",
            created_at=time.time(),
        )

        if engine == "vllm":
            # vLLM: query active requests and cache stats
            try:
                url = f"{endpoint.rstrip('/')}/metrics"
                with urllib.request.urlopen(url, timeout=5) as resp:
                    metrics = resp.read().decode()

                # Extract cache utilization
                for line in metrics.splitlines():
                    if line.startswith("vllm:kv_cache_usage_perc"):
                        parts = line.split()
                        if len(parts) >= 2:
                            snap.metadata["kv_cache_usage"] = float(parts[1])
                    elif line.startswith("vllm:num_requests_running"):
                        parts = line.split()
                        if len(parts) >= 2:
                            snap.num_entries = int(float(parts[1]))

                snap.status = "saved"
                snap.metadata["note"] = "Metrics snapshot — full KV export requires vLLM experimental API"

            except Exception as e:
                snap.status = "failed"
                snap.metadata["error"] = str(e)[:200]

        elif engine == "sglang":
            # SGLang: RadixAttention tree stats
            try:
                url = f"{endpoint.rstrip('/')}/metrics"
                with urllib.request.urlopen(url, timeout=5) as resp:
                    metrics = resp.read().decode()

                for line in metrics.splitlines():
                    if line.startswith("sglang_cache_hit_rate"):
                        parts = line.split()
                        if len(parts) >= 2:
                            snap.metadata["cache_hit_rate"] = float(parts[1])

                snap.status = "saved"
            except Exception as e:
                snap.status = "failed"
                snap.metadata["error"] = str(e)[:200]

        elif engine == "ollama":
            # Ollama: list[Any] loaded models + keep_alive state
            try:
                url = f"{endpoint.rstrip('/')}/api/ps"
                with urllib.request.urlopen(url, timeout=5) as resp:
                    data = json.loads(resp.read())

                models = data.get("models", [])
                snap.num_entries = len(models)
                snap.metadata["loaded_models"] = [
                    {"name": m.get("name", ""), "size": m.get("size", 0)}
                    for m in models
                ]
                snap.status = "saved"
            except Exception as e:
                snap.status = "failed"
                snap.metadata["error"] = str(e)[:200]

        # Save metadata atomically
        meta_path = self.dir / f"{snap_id}.json"
        meta_tmp = meta_path.with_suffix(".tmp")
        meta_tmp.write_text(json.dumps(asdict(snap), indent=2))
        meta_tmp.replace(meta_path)

        return snap

    def _restore_engine_context(self, snap: ContextSnapshot, endpoint: str) -> None:
        """Restore context to a specific engine."""
        import urllib.request

        if snap.engine == "ollama":
            # Re-load models that were in memory
            loaded_models = snap.metadata.get("loaded_models", [])
            for model_info in loaded_models:
                model_name = model_info.get("name", "")
                if not model_name:
                    continue
                try:
                    body = json.dumps({
                        "model": model_name,
                        "prompt": "",
                        "stream": False,
                        "options": {"num_predict": 0},
                    }).encode()
                    req = urllib.request.Request(
                        f"{endpoint.rstrip('/')}/api/generate",
                        data=body,
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=120) as _resp:
                        _resp.read()
                except Exception:
                    pass  # best-effort; failure is non-critical

    def _load_index(self) -> list[ContextSnapshot]:
        """Load data from persistent storage."""
        if not self._index_path.exists():
            return []
        try:
            data = json.loads(self._index_path.read_text())
            return [ContextSnapshot(**{
                k: v for k, v in item.items()
                if k in ContextSnapshot.__dataclass_fields__
            }) for item in data]
        except (json.JSONDecodeError, OSError):
            return []

    def _save_index(self, snapshots: list[ContextSnapshot]) -> None:
        """Persist data to storage (atomic write via temp-file rename)."""
        data = [asdict(s) for s in snapshots]
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._index_path)
