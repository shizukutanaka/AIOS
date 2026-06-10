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

# ── Timeouts (seconds) ───────────────────────────────
ENGINE_HEALTH_TIMEOUT = 5
PROXY_UPSTREAM_TIMEOUT = 120
PROXY_EMBED_TIMEOUT = 60
CLOUD_FALLBACK_TIMEOUT = 30

# ── SLO Defaults ──────────────────────────────────────
SLO_TTFT_MS = 500           # Time-to-first-token target
SLO_TPS = 50                # Tokens-per-second target
SLO_CHECK_INTERVAL = 15     # Governor check interval (seconds)

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
AICTL_VERSION = "1.6.0"

# ── Test Ports (for E2E / gate / demo) ────────────────
TEST_ENGINE_PORT = 19960
TEST_DAEMON_PORT = 19961
TEST_BENCH_PORT = 19977
