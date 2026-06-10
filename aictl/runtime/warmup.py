"""Auto warmup: preload models to reduce cold-start latency.

Tracks model usage frequency and preloads the top N models
on system startup or when idle. Works with Ollama's keep_alive
and vLLM's model loading.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from aictl.core.state import StateStore


@dataclass
class UsageRecord:
    model: str
    engine: str
    count: int = 0
    last_used: float = 0.0
    avg_load_time_ms: float = 0.0


class WarmupManager:
    """Track model usage and preload frequently used models."""

    def __init__(self, store: StateStore):
        """Initialize warmup manager."""
        self.store = store
        self._usage_path = store.dir / "model_usage.json"

    def record_use(self, model: str, engine: str, load_time_ms: float = 0) -> None:
        """Record a model usage event."""
        usage = self._load_usage()
        key = f"{engine}:{model}"
        if key in usage:
            rec = usage[key]
            rec["count"] += 1
            rec["last_used"] = time.time()
            if load_time_ms > 0:
                old_avg = rec.get("avg_load_time_ms", 0)
                count = rec["count"]
                rec["avg_load_time_ms"] = (old_avg * (count - 1) + load_time_ms) / count
        else:
            usage[key] = {
                "model": model,
                "engine": engine,
                "count": 1,
                "last_used": time.time(),
                "avg_load_time_ms": load_time_ms,
            }
        self._save_usage(usage)

    def get_warmup_candidates(self, top_n: int = 3) -> list[UsageRecord]:
        """Return the top N most-used models for preloading."""
        usage = self._load_usage()
        records: list[UsageRecord] = []
        for key, data in usage.items():
            records.append(UsageRecord(
                model=data.get("model", ""),
                engine=data.get("engine", ""),
                count=data.get("count", 0),
                last_used=data.get("last_used", 0),
                avg_load_time_ms=data.get("avg_load_time_ms", 0),
            ))

        # Score: weight recent usage higher
        now = time.time()
        scored = []
        for r in records:
            recency = max(0, 1.0 - (now - r.last_used) / (7 * 86400))  # decay over 7 days
            score = r.count * 0.6 + recency * 100 * 0.4
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_n]]

    def warmup(self, candidates: list[UsageRecord] | None = None) -> list[dict[str, Any]]:
        """Preload models. Returns results for each attempt."""
        if candidates is None:
            candidates = self.get_warmup_candidates()

        results: list[dict[str, Any]] = []
        for rec in candidates:
            result = {"model": rec.model, "engine": rec.engine, "status": "skipped"}

            if rec.engine == "ollama":
                result = self._warmup_ollama(rec.model)
            elif rec.engine in ("vllm", "sglang"):
                result = {"model": rec.model, "engine": rec.engine,
                          "status": "skipped", "reason": "vLLM/SGLang models load on container start"}

            results.append(result)
        return results

    def _warmup_ollama(self, model: str) -> dict[str, Any]:
        """Warm up an Ollama model by sending a minimal generate request."""
        import urllib.request
        import urllib.error

        result = {"model": model, "engine": "ollama", "status": "error"}

        try:
            # First check if model exists
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as resp:
                data = json.loads(resp.read())
                available = [m.get("name", "") for m in data.get("models", [])]
                if model not in available and not any(model in m for m in available):
                    # Pull model; propagate failure so caller gets a clear error
                    pull = subprocess.run(["ollama", "pull", model],
                                          capture_output=True, timeout=300)
                    if pull.returncode != 0:
                        result["error"] = pull.stderr.decode(errors="replace").strip()[:200]
                        return result

            # Send minimal request to load into memory
            t0 = time.monotonic()
            body = json.dumps({
                "model": model,
                "prompt": "Hi",
                "stream": False,
                "options": {"num_predict": 1},
            }).encode()
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp.read()
            load_ms = (time.monotonic() - t0) * 1000
            result["status"] = "loaded"
            result["load_time_ms"] = round(load_ms, 1)

        except Exception as e:
            result["error"] = str(e)[:100]

        return result

    def _load_usage(self) -> dict[str, dict[str, Any]]:
        """Load data from persistent storage."""
        if not self._usage_path.exists():
            return {}
        try:
            data: dict[str, dict[str, Any]] = json.loads(self._usage_path.read_text())
            return data
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_usage(self, usage: dict[str, dict[str, Any]]) -> None:
        """Persist data to storage."""
        self._usage_path.write_text(json.dumps(usage, indent=2))
