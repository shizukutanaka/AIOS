"""Configuration: persistent settings for aictl.

Stored in ~/.aios/config.json. Covers:
  - Engine endpoints (vllm, ollama, sglang)
  - SLO targets
  - Trust policy mode
  - Daemon settings (host, port)
  - Quadlet mode (rootless/root)
  - Default recipe
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aictl.core.state import DEFAULT_STATE_DIR


@dataclass
class EngineEndpoints:
    vllm: str = "http://localhost:8000"
    ollama: str = "http://localhost:11434"
    sglang: str = "http://localhost:30000"

    def to_dict(self) -> dict[str, str]:
        """To dict."""
        return {"vllm": self.vllm, "ollama": self.ollama, "sglang": self.sglang}


@dataclass
class SLOConfig:
    ttft_p95_ms: float = 500.0
    itl_p95_ms: float = 50.0
    tokens_per_sec_min: float = 10.0
    error_rate_max: float = 0.05
    queue_depth_max: int = 100
    kv_cache_max: float = 0.9
    psi_memory_some_max: float = 25.0


@dataclass
class DaemonConfig:
    host: str = "127.0.0.1"
    port: int = 7700


@dataclass
class Config:
    engines: EngineEndpoints = field(default_factory=EngineEndpoints)
    slo: SLOConfig = field(default_factory=SLOConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    trust_policy: str = "warn"        # enforce | warn | disabled
    quadlet_rootless: bool = True
    default_recipe: str = "local-chat"
    model_cache_dir: str = ""
    log_level: str = "info"


def load_config(state_dir: Path | None = None) -> Config:
    """Load config from ~/.aios/config.json, or return defaults."""
    path = (state_dir or DEFAULT_STATE_DIR) / "config.json"
    if not path.exists():
        return Config()

    try:
        data = json.loads(path.read_text())
        c = Config()

        if "engines" in data:
            e = data["engines"]
            c.engines = EngineEndpoints(
                vllm=e.get("vllm", c.engines.vllm),
                ollama=e.get("ollama", c.engines.ollama),
                sglang=e.get("sglang", c.engines.sglang),
            )
        if "slo" in data:
            s = data["slo"]
            c.slo = SLOConfig(**{k: s[k] for k in SLOConfig.__dataclass_fields__ if k in s})
        if "daemon" in data:
            d = data["daemon"]
            c.daemon = DaemonConfig(**{k: d[k] for k in DaemonConfig.__dataclass_fields__ if k in d})

        c.trust_policy = data.get("trust_policy", c.trust_policy)
        c.quadlet_rootless = data.get("quadlet_rootless", c.quadlet_rootless)
        c.default_recipe = data.get("default_recipe", c.default_recipe)
        c.model_cache_dir = data.get("model_cache_dir", c.model_cache_dir)
        c.log_level = data.get("log_level", c.log_level)

        return c
    except (json.JSONDecodeError, KeyError):
        return Config()


def save_config(config: Config, state_dir: Path | None = None) -> None:
    """Save config."""
    path = (state_dir or DEFAULT_STATE_DIR) / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2))
