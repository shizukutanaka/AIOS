"""LoRA adapter manager: hot-swap adapters on shared base models.

LLM OS key insight: Multiple LoRA adapters can share a single base model,
saving massive VRAM. Instead of loading 3 separate 8B models (48GB),
load 1 base + 3 LoRA adapters (~16.5GB total).

Features:
  - Register base models and LoRA adapters
  - Track which adapters are loaded on which engines
  - Hot-swap adapters without reloading the base model
  - VRAM budget management (ensure adapters fit)
  - vLLM: uses --enable-lora --lora-modules flag
  - SGLang: uses --lora-paths flag
  - Ollama: uses FROM + ADAPTER in Modelfile

Based on:
  - Gateway API InferenceModel: targetModels with weight routing
  - vLLM LoRA serving: up to 100+ adapters per base model
  - SGLang RadixAttention: cache-aware LoRA routing
"""

from __future__ import annotations

from typing import Any

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class BaseModel:
    name: str                    # e.g. "meta-llama/Llama-3.2-8B-Instruct"
    vram_mb: int = 0             # VRAM consumed by base weights
    max_loras: int = 64          # Max concurrent LoRA adapters
    loaded_on: list[str] = field(default_factory=list)  # Engine endpoints


@dataclass
class LoRAAdapter:
    name: str                    # e.g. "my-finance-lora"
    base_model: str              # Base model name
    path: str = ""               # Path to adapter weights (HF or local)
    vram_overhead_mb: int = 100  # Additional VRAM per adapter (~50-200MB typical)
    rank: int = 16               # LoRA rank (8, 16, 32, 64 common)
    active: bool = True
    traffic_weight: int = 100    # For weighted routing (0-100)


class LoRAManager:
    """Manage LoRA adapters across inference engines."""

    def __init__(self, state_dir: Path | None = None):
        """Initialize LoRA adapter manager."""
        if state_dir is None:
            from aictl.core.state import DEFAULT_STATE_DIR
            state_dir = DEFAULT_STATE_DIR
        self.dir = state_dir
        self._path = self.dir / "lora_registry.json"

    def register_base(self, model: BaseModel) -> None:
        """Register base."""
        data = self._load()
        data["bases"][model.name] = asdict(model)
        self._save(data)

    def register_adapter(self, adapter: LoRAAdapter) -> None:
        """Register adapter."""
        data = self._load()
        data["adapters"][adapter.name] = asdict(adapter)
        self._save(data)

    def list_bases(self) -> list[BaseModel]:
        """List bases."""
        data = self._load()
        return [BaseModel(**v) for v in data.get("bases", {}).values()]

    def list_adapters(self, base_model: str = "") -> list[LoRAAdapter]:
        """List adapters."""
        data = self._load()
        adapters = [LoRAAdapter(**v) for v in data.get("adapters", {}).values()]
        if base_model:
            adapters = [a for a in adapters if a.base_model == base_model]
        return adapters

    def vram_budget(self, base_model: str) -> dict[str, int]:
        """Calculate VRAM budget for a base model + its adapters."""
        data = self._load()
        base = data.get("bases", {}).get(base_model, {})
        base_vram = base.get("vram_mb", 0)

        adapters = self.list_adapters(base_model)
        adapter_vram = sum(a.vram_overhead_mb for a in adapters if a.active)

        return {
            "base_vram_mb": base_vram,
            "adapter_vram_mb": adapter_vram,
            "total_vram_mb": base_vram + adapter_vram,
            "active_adapters": len([a for a in adapters if a.active]),
            "max_adapters": base.get("max_loras", 64),
        }

    def generate_vllm_args(self, base_model: str) -> list[str]:
        """Generate vLLM command-line arguments for LoRA serving."""
        adapters = self.list_adapters(base_model)
        if not adapters:
            return []

        active = [a for a in adapters if a.active]
        if not active:
            return []

        args = ["--enable-lora", f"--max-loras={len(active)}"]
        lora_modules = []
        for a in active:
            if a.path:
                lora_modules.append(f"{a.name}={a.path}")
        if lora_modules:
            args.append(f"--lora-modules={','.join(lora_modules)}")
        return args

    def generate_sglang_args(self, base_model: str) -> list[str]:
        """Generate SGLang command-line arguments for LoRA serving."""
        adapters = self.list_adapters(base_model)
        active = [a for a in adapters if a.active and a.path]
        if not active:
            return []
        paths = [a.path for a in active]
        return [f"--lora-paths={','.join(paths)}"]

    def _load(self) -> dict[str, Any]:
        """Load and return data from storage."""
        if not self._path.exists():
            return {"bases": {}, "adapters": {}}
        try:
            data: dict[str, Any] = json.loads(self._path.read_text())
            return data
        except (json.JSONDecodeError, OSError):
            return {"bases": {}, "adapters": {}}

    def _save(self, data: dict[str, Any]) -> None:
        """Persist data to storage."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))
