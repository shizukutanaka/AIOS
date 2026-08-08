"""Centralized constants for the aictl project.

All magic numbers, default ports, URLs, and configuration values
are defined here. No module should hardcode these values.

Design principle (Rob Pike): "Constants should be defined once,
in one place, and used everywhere."
"""

from __future__ import annotations

# ── Network Defaults ──────────────────────────────────
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 7700
PROXY_PORT = 8080
MOCK_ENGINE_PORT = 9999

# ── Engine Defaults ───────────────────────────────────
VLLM_DEFAULT_PORT = 8000
SGLANG_DEFAULT_PORT = 30000
OLLAMA_DEFAULT_PORT = 11434
VLLM_DEFAULT_URL = f"http://localhost:{VLLM_DEFAULT_PORT}"
SGLANG_DEFAULT_URL = f"http://localhost:{SGLANG_DEFAULT_PORT}"
OLLAMA_DEFAULT_URL = f"http://localhost:{OLLAMA_DEFAULT_PORT}"

# Opt-in engines (IMPROVEMENTS.md item D): unlike vLLM/SGLang/Ollama above,
# these are NEVER probed unless a user explicitly sets a URL via
# `aictl config set engines.<name> <url>` — EngineEndpoints.to_dict() only
# includes them when non-empty, so out-of-the-box discover_engines()/status/
# health behavior is completely unchanged.
LMDEPLOY_DEFAULT_PORT = 23333       # `lmdeploy serve api_server` default
TRT_LLM_DEFAULT_PORT = 8000         # `trtllm-serve` documented default
LM_STUDIO_DEFAULT_PORT = 1234       # LM Studio local server default
LMDEPLOY_DEFAULT_URL = f"http://localhost:{LMDEPLOY_DEFAULT_PORT}"
TRT_LLM_DEFAULT_URL = f"http://localhost:{TRT_LLM_DEFAULT_PORT}"
LM_STUDIO_DEFAULT_URL = f"http://localhost:{LM_STUDIO_DEFAULT_PORT}"

# ── Timeouts (seconds) ───────────────────────────────
ENGINE_HEALTH_TIMEOUT = 5
PROXY_UPSTREAM_TIMEOUT = 120
PROXY_EMBED_TIMEOUT = 60
CLOUD_FALLBACK_TIMEOUT = 30
HOOK_WEBHOOK_TIMEOUT = 5     # aictl.core.hook_dispatch webhook POST
HOOK_SCRIPT_TIMEOUT = 10    # aictl.core.hook_dispatch local script exec
GUARD_MODEL_CHECK_TIMEOUT = 5   # aictl.core.guard.make_llm_content_check
EMBEDDING_MODEL_DETECT_TIMEOUT = 3   # aictl.sdk._detect_embedding_model /v1/models probe
RERANK_TIMEOUT = 5   # aictl.core.rerank.rerank TEI-compatible /rerank POST
CONFORMANCE_PROBE_TIMEOUT = 5   # aictl.runtime.conformance per-probe HTTP timeout

# vLLM OffloadingConnector (KV prefix-cache offload to pinned host memory).
# cpu_bytes_to_use is PINNED (page-locked) host memory: it is unswappable and
# taken away from the OS for the engine's lifetime, so over-allocating it
# destabilizes the whole host, not just the engine. These bounds keep the
# recommendation conservative.
KV_OFFLOAD_HOST_RAM_FRACTION = 0.25   # max share of host RAM to pin
KV_OFFLOAD_MIN_FREE_RAM_MB = 8192     # never pin if it would leave the host under this
KV_OFFLOAD_MIN_BYTES = 4 * 1024**3    # below this the offload tier isn't worth its overhead
KV_OFFLOAD_BLOCK_SIZE = 64            # per the connector's documented example

# ── Guard model-check verdict cache (IMPROVEMENTS.md item P) ─────────
# DoS hardening (arXiv:2606.14517 "From Shield to Target"): a flood of
# identical/near-identical prompts must not re-trigger the upstream guard
# model on every single request.
GUARD_MODEL_CHECK_CACHE_MAX_ENTRIES = 256

# ── SLO Defaults ──────────────────────────────────────
SLO_TTFT_MS = 500           # Time-to-first-token target
SLO_TPS = 50                # Tokens-per-second target
SLO_CHECK_INTERVAL = 15     # Governor check interval (seconds)

# ── Scheduler ─────────────────────────────────────────
# A persisted schedule interval below this floor can busy-fire on every
# scheduler tick instead of respecting a real cadence (worst case: a
# non-positive interval never advances next_run past "now" at all).
MIN_SCHEDULE_INTERVAL_SECS = 60

# ── Security ──────────────────────────────────────────
API_KEY_PREFIX = "aios-"
API_KEY_LENGTH = 32          # Characters after prefix
STATE_DIR_PERMISSIONS = 0o700
MAX_REQUEST_BODY = 1 * 1024 * 1024   # 1 MiB — daemon POST body cap

# ── Container / K8s ──────────────────────────────────
VLLM_IMAGE = "vllm/vllm-openai:v0.19.0"
SGLANG_IMAGE = "lmsys/sglang:v0.5.9"
OLLAMA_IMAGE = "ollama/ollama:0.20"
BOOTC_BASE_IMAGE = "quay.io/fedora/fedora-bootc:42"

# ── Model Defaults ────────────────────────────────────
DEFAULT_MAX_MODEL_LEN = 32768
DEFAULT_GPU_MEMORY_UTIL = 0.9
DEFAULT_MAX_TOKENS = 1000
MAX_LORA_ADAPTERS = 64

# ── Metering ──────────────────────────────────────────
PRICE_PER_MILLION_INPUT = 0.15   # USD
PRICE_PER_MILLION_OUTPUT = 0.60  # USD

# ── Versions ──────────────────────────────────────────
AICTL_VERSION = "1.7.0"

# ── Test Ports (for E2E / gate / demo) ────────────────
TEST_ENGINE_PORT = 19960
TEST_DAEMON_PORT = 19961
TEST_BENCH_PORT = 19977
