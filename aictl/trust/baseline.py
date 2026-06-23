"""Model integrity baseline — trust-on-first-use drift detection.

`verify_digest` answers "does this file match a digest I was *given*?". This
module answers the complementary question operators actually face after they've
already trusted a model: "do my local model files still have the same bytes they
had when I trusted them, or has something (bit-rot, tampering, a bad sync)
changed them since?".

It records a SHA-256 baseline for a model file or directory on first use, then
re-hashes on demand and reports per-file drift: ok / changed / missing / new.
Pure stdlib; the baseline is a JSON sidecar in the state dir.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from aictl.trust.verify import sha256_file

# Weight-file extensions worth baselining inside a model directory.
MODEL_EXTS = frozenset({".gguf", ".safetensors", ".bin", ".pt", ".pth", ".onnx"})


def model_files(path: Path) -> list[Path]:
    """The set of files to baseline for `path`.

    A single file is taken as-is; a directory is scanned recursively for known
    weight-file extensions (config/tokenizer files are ignored — they are not the
    integrity-critical bytes and churn for benign reasons).
    """
    path = Path(path)
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.rglob("*")
                      if p.is_file() and p.suffix.lower() in MODEL_EXTS)
    return []


class BaselineStore:
    """Persists `{abs_path: {digest, size, recorded_at}}` as a JSON sidecar."""

    def __init__(self, state_dir: Path | None = None):
        if state_dir is None:
            from aictl.core.state import DEFAULT_STATE_DIR
            state_dir = DEFAULT_STATE_DIR
        self.dir = Path(state_dir)
        self.path = self.dir / "trust_baseline.json"

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def record(self, target: str | Path) -> list[dict[str, Any]]:
        """Baseline every model file under `target`. Returns the recorded rows."""
        files = model_files(Path(target))
        data = self._load()
        recorded: list[dict[str, Any]] = []
        now = time.time()
        for f in files:
            key = str(f.resolve())
            entry = {
                "digest": sha256_file(f),
                "size": f.stat().st_size,
                "recorded_at": now,
            }
            data[key] = entry
            recorded.append({"path": key, **entry})
        self._save(data)
        return recorded

    def check(self, target: str | Path) -> list[dict[str, Any]]:
        """Compare current files under `target` against the baseline.

        Status per file:
          ok      — present in baseline and digest matches
          changed — present in baseline but digest differs (drift!)
          new     — exists on disk but was never baselined
          missing — in baseline but no longer on disk
        """
        data = self._load()
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        for f in model_files(Path(target)):
            key = str(f.resolve())
            seen.add(key)
            base = data.get(key)
            if base is None:
                results.append({"path": key, "status": "new"})
                continue
            actual = sha256_file(f)
            status = "ok" if actual == base["digest"] else "changed"
            results.append({
                "path": key, "status": status,
                "expected": base["digest"], "actual": actual,
            })

        # A baselined file that lives under `target` but is gone from disk.
        target_prefix = str(Path(target).resolve())
        for key, base in data.items():
            if key in seen:
                continue
            if key == target_prefix or key.startswith(target_prefix.rstrip("/") + "/"):
                results.append({"path": key, "status": "missing",
                                "expected": base["digest"]})

        return sorted(results, key=lambda r: r["path"])

    def list_all(self) -> dict[str, dict[str, Any]]:
        """Every recorded baseline entry."""
        return self._load()


def worst_status(results: list[dict[str, Any]]) -> str:
    """The most severe status across results (drives the exit code)."""
    order = ["changed", "missing", "new", "ok"]
    present = {r["status"] for r in results}
    for s in order:
        if s in present:
            return s
    return "ok"
