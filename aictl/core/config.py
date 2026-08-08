"""Configuration: persistent settings for aictl.

Stored in ~/.aios/config.json. Covers:
  - Engine endpoints (vllm, ollama, sglang)
  - SLO targets
  - Trust policy mode
  - Daemon settings (host, port)
  - Quadlet mode (rootless/root)
  - Default recipe
  - Cloud fallback (provider, model, api_key)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aictl.core.state import DEFAULT_STATE_DIR
from aictl.core.atomicio import atomic_write_text
from aictl.core.constants import (
    DAEMON_PORT, VLLM_DEFAULT_URL, OLLAMA_DEFAULT_URL, SGLANG_DEFAULT_URL,
)


@dataclass
class EngineEndpoints:
    vllm: str = VLLM_DEFAULT_URL
    ollama: str = OLLAMA_DEFAULT_URL
    sglang: str = SGLANG_DEFAULT_URL
    # Opt-in engines (IMPROVEMENTS.md item D) — empty by default and
    # excluded from to_dict() unless set, so discover_engines()/status/
    # health/demo/gate are completely unaffected until a user explicitly
    # runs `aictl config set engines.lmdeploy <url>` (etc).
    lmdeploy: str = ""
    tensorrt_llm: str = ""
    lm_studio: str = ""

    def to_dict(self) -> dict[str, str]:
        """To dict. Opt-in engines only appear once configured (non-empty)."""
        d = {"vllm": self.vllm, "ollama": self.ollama, "sglang": self.sglang}
        if self.lmdeploy:
            d["lmdeploy"] = self.lmdeploy
        if self.tensorrt_llm:
            d["tensorrt_llm"] = self.tensorrt_llm
        if self.lm_studio:
            d["lm_studio"] = self.lm_studio
        return d


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
    port: int = DAEMON_PORT


@dataclass
class FallbackSettings:
    """Cloud-fallback settings (aictl.runtime.fallback).

    `api_key` is a secret: `save_config` writes it via `atomic_write_text`
    (0o600-by-construction, since the temp file `mkstemp` creates is owner-only
    and no wider mode is ever applied on replace) but `aictl config show` MUST
    redact it — see cmd/config.py's run_show — so it never lands in terminal
    scrollback, screen recordings, or a pasted support-ticket transcript.
    """
    enabled: bool = False
    provider: str = ""
    api_key: str = ""
    model: str = ""
    max_tokens: int = 1000
    timeout_s: int = 30


@dataclass
class Config:
    engines: EngineEndpoints = field(default_factory=EngineEndpoints)
    slo: SLOConfig = field(default_factory=SLOConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    fallback: FallbackSettings = field(default_factory=FallbackSettings)
    trust_policy: str = "warn"        # enforce | warn | disabled
    guard_policy: str = "off"         # enforce | warn | off — content policy (injection/jailbreak)
    guard_redact_output: bool = False  # redact PII from completion responses before returning
    # Live fair-share admission (IMPROVEMENTS.md item M). "off" leaves the
    # serving path untouched; "warn" logs what it would have deferred without
    # deferring anything; "enforce" actually rejects. Default off — this is
    # the first thing here that can refuse a request on fairness grounds.
    fair_share_policy: str = "off"     # enforce | warn | off
    fair_share_yield_ratio: float = 2.0  # defer above this multiple of an even share
    # Optional model-based content check (IMPROVEMENTS.md item G, proposal 3):
    # empty endpoint = disabled (zero-dep default, no network call ever made).
    # Set both to point the proxy's guard gate at a local Llama-Guard-style
    # model, e.g. `aictl config set guard_model_check_endpoint
    # http://localhost:11434` with `aictl config set guard_model_check_model
    # llama-guard3`.
    guard_model_check_endpoint: str = ""
    guard_model_check_model: str = "llama-guard3"
    # Cosine-similarity floor for a semantic-cache hit (IMPROVEMENTS.md item B).
    # Must match aictl.core.sem_cache.DEFAULT_THRESHOLD -- kept as a separate
    # literal (not imported) so core/config.py has no dependency on
    # core/sem_cache.py; get_default_cache() reads this value at first
    # construction, only overriding the module's own built-in default when
    # a user has actually configured one.
    cache_similarity_floor: float = 0.92
    # Embedding-kNN request router (IMPROVEMENTS.md item C-1): False by
    # default = a true no-op, byte-identical to the regex-only router that
    # existed before this feature -- zero embed_text() calls, zero disk
    # I/O. When enabled, kNN is consulted ONLY as a confidence-gated
    # tie-breaker: the always-on regex scorer (score_complexity/
    # classify_complexity) still runs first and is the sole verdict
    # unless the score falls within route_knn_margin of a tier boundary
    # AND real (non-fallback) embeddings are available AND neighbor
    # agreement clears route_knn_min_agreement AND the kNN verdict is an
    # adjacent tier (never a 2-tier jump). Any failure at any step
    # silently abstains to the regex verdict -- see
    # aictl/cmd/route.py's route_tier_gated().
    route_knn_enabled: bool = False
    route_knn_margin: int = 5           # +/- distance from the 30/60 boundary
    route_knn_k: int = 5                # neighbors consulted
    route_knn_min_agreement: float = 0.8  # fraction of neighbors that must agree
    # Pluggable reranker for RAG search results (IMPROVEMENTS.md item A-3):
    # empty endpoint = disabled (zero-dep default, no network call ever
    # made). Targets a TEI-compatible (HuggingFace Text Embeddings
    # Inference) /rerank endpoint -- the only self-hosted reranker contract
    # independently verified against its own OpenAPI spec (vLLM's /rerank
    # claims Cohere-compatibility but its exact field names could not be
    # confirmed against vLLM's own docs). Set both to point `rag search
    # --rerank`/`rag ask --rerank` at a local cross-encoder, e.g. `aictl
    # config set rerank_endpoint http://localhost:8080` with `aictl config
    # set rerank_model bge-reranker-v2-m3`.
    rerank_endpoint: str = ""
    rerank_model: str = ""
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
        # A list/scalar-rooted config.json parses cleanly, but `"engines" in
        # data` silently passes (False) for a list while the very next
        # `data.get("trust_policy", ...)` raises AttributeError ('list' object
        # has no attribute 'get') — uncaught, surfaced as "report a bug" for
        # nearly every command (almost all of them call load_config). Guard the
        # root type up front and degrade to defaults, matching the V7 pattern
        # used by every other persisted-state loader in the project.
        if not isinstance(data, dict):
            return Config()
        c = Config()

        if "engines" in data:
            e = data["engines"]
            c.engines = EngineEndpoints(
                vllm=e.get("vllm", c.engines.vllm),
                ollama=e.get("ollama", c.engines.ollama),
                sglang=e.get("sglang", c.engines.sglang),
                lmdeploy=e.get("lmdeploy", c.engines.lmdeploy),
                tensorrt_llm=e.get("tensorrt_llm", c.engines.tensorrt_llm),
                lm_studio=e.get("lm_studio", c.engines.lm_studio),
            )
        if "slo" in data:
            s = data["slo"]
            c.slo = SLOConfig(**{k: s[k] for k in SLOConfig.__dataclass_fields__ if k in s})
        if "daemon" in data:
            d = data["daemon"]
            c.daemon = DaemonConfig(**{k: d[k] for k in DaemonConfig.__dataclass_fields__ if k in d})
        if "fallback" in data:
            fb = data["fallback"]
            c.fallback = FallbackSettings(
                **{k: fb[k] for k in FallbackSettings.__dataclass_fields__ if k in fb})

        c.trust_policy = data.get("trust_policy", c.trust_policy)
        c.guard_policy = data.get("guard_policy", c.guard_policy)
        c.guard_redact_output = data.get("guard_redact_output", c.guard_redact_output)
        c.fair_share_policy = data.get("fair_share_policy", c.fair_share_policy)
        c.fair_share_yield_ratio = data.get("fair_share_yield_ratio",
                                            c.fair_share_yield_ratio)
        c.guard_model_check_endpoint = data.get("guard_model_check_endpoint",
                                                c.guard_model_check_endpoint)
        c.guard_model_check_model = data.get("guard_model_check_model",
                                             c.guard_model_check_model)
        c.cache_similarity_floor = data.get("cache_similarity_floor", c.cache_similarity_floor)
        c.route_knn_enabled = data.get("route_knn_enabled", c.route_knn_enabled)
        c.route_knn_margin = data.get("route_knn_margin", c.route_knn_margin)
        c.route_knn_k = data.get("route_knn_k", c.route_knn_k)
        c.route_knn_min_agreement = data.get("route_knn_min_agreement", c.route_knn_min_agreement)
        c.rerank_endpoint = data.get("rerank_endpoint", c.rerank_endpoint)
        c.rerank_model = data.get("rerank_model", c.rerank_model)
        c.quadlet_rootless = data.get("quadlet_rootless", c.quadlet_rootless)
        c.default_recipe = data.get("default_recipe", c.default_recipe)
        c.model_cache_dir = data.get("model_cache_dir", c.model_cache_dir)
        c.log_level = data.get("log_level", c.log_level)

        return c
    except (json.JSONDecodeError, KeyError, OSError, AttributeError, TypeError):
        return Config()


def save_config(config: Config, state_dir: Path | None = None) -> None:
    """Save config."""
    path = (state_dir or DEFAULT_STATE_DIR) / "config.json"
    atomic_write_text(path, json.dumps(asdict(config), indent=2))
