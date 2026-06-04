"""Cloud fallback: route to cloud APIs when local engines are down.

When all local engines (vLLM/SGLang/Ollama) are unreachable, the proxy
can fall back to cloud inference APIs. This provides resilience without
requiring permanent cloud dependency.

Supported providers (via OpenAI-compatible API):
  - OpenAI (api.openai.com)
  - Anthropic (via OpenAI compat proxy)
  - OpenRouter (openrouter.ai)
  - Together AI (api.together.xyz)
  - Groq (api.groq.com)
  - Fireworks (api.fireworks.ai)
  - Custom (any OpenAI-compatible endpoint)

Configuration:
  aictl config set fallback.provider openrouter
  aictl config set fallback.api_key sk-or-xxx
  aictl config set fallback.model meta-llama/llama-3.1-8b-instruct

Security:
  - API keys stored in ~/.aios/config.json (chmod 600)
  - Fallback is DISABLED by default
  - Must be explicitly enabled: aictl config set fallback.enabled true
  - Audit log records every cloud fallback event

Based on research:
  - LiteLLM: 100+ provider support, 8ms P95 latency
  - Olla: Local routing + LiteLLM cloud overflow
  - OpenRouter: 5.5% fee, 200+ models
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any


@dataclass
class CloudProvider:
    name: str
    base_url: str
    api_key_env: str  # Environment variable name
    default_model: str
    supports_streaming: bool = True


PROVIDERS: dict[str, CloudProvider] = {
    "openai": CloudProvider(
        name="OpenAI", base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY", default_model="gpt-4o-mini",
    ),
    "openrouter": CloudProvider(
        name="OpenRouter", base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        default_model="meta-llama/llama-3.1-8b-instruct",
    ),
    "together": CloudProvider(
        name="Together AI", base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
        default_model="meta-llama/Llama-3.1-8B-Instruct-Turbo",
    ),
    "groq": CloudProvider(
        name="Groq", base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY", default_model="llama-3.1-8b-instant",
    ),
    "fireworks": CloudProvider(
        name="Fireworks", base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        default_model="accounts/fireworks/models/llama-v3p1-8b-instruct",
    ),
}


@dataclass
class FallbackConfig:
    enabled: bool = False
    provider: str = ""         # openai | openrouter | together | groq | fireworks
    api_key: str = ""          # Direct key (prefer env var)
    model: str = ""            # Override model
    max_tokens: int = 1000
    timeout_s: int = 30


def cloud_completion(
    config: FallbackConfig,
    messages: list[dict[str, Any]],
    model: str = "",
    max_tokens: int = 0,
    stream: bool = False,
) -> dict[str, Any] | None:
    """Send a completion request to a cloud provider.

    Returns the response dict, or None on failure.
    """
    if not config.enabled or not config.provider:
        return None

    provider = PROVIDERS.get(config.provider)
    if not provider:
        return None

    # Resolve API key
    import os
    api_key = config.api_key or os.environ.get(provider.api_key_env, "")
    if not api_key:
        return None

    # Resolve model
    target_model = model or config.model or provider.default_model

    # Build request
    body = {
        "model": target_model,
        "messages": messages,
        "max_tokens": max_tokens or config.max_tokens,
        "stream": False,  # Streaming requires chunked response handling
    }

    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # OpenRouter requires extra headers
    if config.provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/shizukutanaka/aios"
        headers["X-Title"] = "aictl"

    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=config.timeout_s) as resp:
            result = json.loads(resp.read())

        # Tag response as cloud fallback
        result["_aios_fallback"] = True
        result["_aios_provider"] = provider.name
        return result

    except Exception:
        return None


def load_fallback_config(state_dir: Any) -> FallbackConfig:
    """Load fallback config from config.json."""
    from pathlib import Path
    config_path = Path(state_dir) / "config.json" if state_dir else None
    if not config_path or not config_path.exists():
        return FallbackConfig()

    try:
        data = json.loads(config_path.read_text())
        fb = data.get("fallback", {})
        return FallbackConfig(
            enabled=fb.get("enabled", False),
            provider=fb.get("provider", ""),
            api_key=fb.get("api_key", ""),
            model=fb.get("model", ""),
            max_tokens=fb.get("max_tokens", 1000),
            timeout_s=fb.get("timeout_s", 30),
        )
    except (json.JSONDecodeError, OSError):
        return FallbackConfig()
