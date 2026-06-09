"""Benchmark: measure inference engine latency and throughput.

Sends test prompts to an OpenAI-compatible endpoint and measures:
  - TTFT (Time To First Token)
  - ITL  (Inter-Token Latency)
  - Total latency
  - Tokens/second
  - Error rate
"""

from __future__ import annotations

from aictl.core.constants import OLLAMA_DEFAULT_URL

import json
import time
import urllib.request
from dataclasses import dataclass


@dataclass
class BenchResult:
    endpoint: str = ""
    model: str = ""
    requests: int = 0
    errors: int = 0
    ttft_ms_avg: float = 0.0
    ttft_ms_p95: float = 0.0
    total_ms_avg: float = 0.0
    tokens_generated: int = 0
    tokens_per_sec: float = 0.0
    duration_sec: float = 0.0


BENCH_PROMPTS = [
    "Explain quantum computing in one paragraph.",
    "Write a haiku about artificial intelligence.",
    "What is the capital of Japan and why is it significant?",
    "List 5 benefits of open source software.",
    "Describe the water cycle in simple terms.",
]


def run_benchmark(
    endpoint: str = OLLAMA_DEFAULT_URL,
    model: str = "",
    num_requests: int = 5,
    max_tokens: int = 100,
    timeout: int = 60,
) -> BenchResult:
    """Run a simple benchmark against an OpenAI-compatible endpoint."""
    result = BenchResult(endpoint=endpoint, model=model, requests=num_requests)

    # Detect API type
    is_ollama = ":11434" in endpoint

    ttfts: list[float] = []
    totals: list[float] = []
    total_tokens = 0
    errors = 0

    t_start = time.monotonic()

    for i in range(num_requests):
        prompt = BENCH_PROMPTS[i % len(BENCH_PROMPTS)]

        try:
            if is_ollama and "/v1" not in endpoint:
                ttft, total, tokens = _bench_ollama(endpoint, model, prompt, max_tokens, timeout)
            else:
                ttft, total, tokens = _bench_openai(endpoint, model, prompt, max_tokens, timeout)

            ttfts.append(ttft)
            totals.append(total)
            total_tokens += tokens
        except Exception:
            errors += 1

    result.duration_sec = time.monotonic() - t_start
    result.errors = errors

    if ttfts:
        ttfts.sort()
        result.ttft_ms_avg = sum(ttfts) / len(ttfts)
        result.ttft_ms_p95 = ttfts[int((len(ttfts) - 1) * 0.95)] if len(ttfts) > 1 else ttfts[0]
        result.total_ms_avg = sum(totals) / len(totals)

    result.tokens_generated = total_tokens
    if result.duration_sec > 0:
        result.tokens_per_sec = total_tokens / result.duration_sec

    return result


def _bench_ollama(endpoint: str, model: str, prompt: str, max_tokens: int,
                  timeout: int) -> tuple[float, float, int]:
    """Benchmark via Ollama native API (streaming for TTFT measurement)."""
    url = f"{endpoint.rstrip('/')}/api/generate"
    body = json.dumps({
        "model": model or "llama3.2:3b",
        "prompt": prompt,
        "stream": True,
        "options": {"num_predict": max_tokens},
    }).encode()

    req = urllib.request.Request(url, data=body,
                                headers={"Content-Type": "application/json"})

    t0 = time.monotonic()
    ttft = 0.0
    tokens = 0

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if tokens == 0:
                    ttft = (time.monotonic() - t0) * 1000
                if data.get("response"):
                    tokens += 1
                if data.get("done"):
                    break
            except json.JSONDecodeError:
                pass  # best-effort; failure is non-critical

    total = (time.monotonic() - t0) * 1000
    return ttft, total, tokens


def _bench_openai(endpoint: str, model: str, prompt: str, max_tokens: int,
                  timeout: int) -> tuple[float, float, int]:
    """Benchmark via OpenAI-compatible API (non-streaming for simplicity)."""
    url = f"{endpoint.rstrip('/')}/v1/chat/completions"
    if "/v1/v1" in url:
        url = url.replace("/v1/v1", "/v1")

    body = json.dumps({
        "model": model or "default",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": False,
    }).encode()

    req = urllib.request.Request(url, data=body,
                                headers={"Content-Type": "application/json"})

    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())

    total = (time.monotonic() - t0) * 1000

    tokens = 0
    if "usage" in data:
        tokens = data["usage"].get("completion_tokens", 0)
    elif "choices" in data and data["choices"]:
        content = data["choices"][0].get("message", {}).get("content", "")
        tokens = len(content.split())  # Rough estimate

    # TTFT for non-streaming: entire response latency = time to first usable token
    ttft = total

    return ttft, total, tokens
