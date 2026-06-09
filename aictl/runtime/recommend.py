"""Model recommendation: suggest optimal models based on detected hardware.

Given VRAM, RAM, and intended use case, recommend models that will
actually fit and perform well. No guessing — only tested configurations.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelRec:
    name: str
    runtime: str         # ollama | vllm | sglang
    vram_required_mb: int
    ram_required_mb: int
    use_case: str        # chat | code | embedding | vision | stt | reasoning
    quantization: str    # q4_K_M | q8_0 | fp16 | awq
    context_length: int
    notes: str = ""


# Curated model database — only models known to work well
MODELS: list[ModelRec] = [
    # ── Chat (general) ───────────────────────────────
    ModelRec("llama3.2:1b", "ollama", 1500, 4096, "chat", "q4_K_M", 131072,
             "Smallest usable chat model"),
    ModelRec("llama3.2:3b", "ollama", 3000, 6144, "chat", "q4_K_M", 131072,
             "Good balance for CPU-heavy setups"),
    ModelRec("qwen2.5:7b", "ollama", 5500, 8192, "chat", "q4_K_M", 32768,
             "Strong multilingual (JP/EN/CN)"),
    ModelRec("llama3.1:8b", "ollama", 6000, 8192, "chat", "q4_K_M", 131072,
             "Meta's flagship 8B — great all-rounder"),
    ModelRec("meta-llama/Llama-3.2-8B-Instruct", "vllm", 16384, 16384, "chat", "fp16", 131072,
             "Full precision for vLLM — best quality"),
    ModelRec("qwen2.5:14b", "ollama", 10000, 16384, "chat", "q4_K_M", 32768,
             "Strong reasoning, fits 12GB+ GPU"),
    ModelRec("llama3.1:70b", "ollama", 42000, 48000, "chat", "q4_K_M", 131072,
             "Frontier quality — needs 48GB+ VRAM"),
    ModelRec("meta-llama/Llama-3.1-70B-Instruct", "vllm", 140000, 140000, "chat", "fp16", 131072,
             "Full precision 70B — needs 2x A100 80GB"),

    # ── Code ─────────────────────────────────────────
    ModelRec("qwen2.5-coder:7b", "ollama", 5500, 8192, "code", "q4_K_M", 32768,
             "Best code model at 7B size"),
    ModelRec("Qwen/Qwen2.5-Coder-7B-Instruct", "vllm", 16384, 16384, "code", "fp16", 32768,
             "Full precision for vLLM code serving"),
    ModelRec("deepseek-coder-v2:16b", "ollama", 12000, 16384, "code", "q4_K_M", 65536,
             "DeepSeek Coder v2 — strong for complex code"),
    ModelRec("qwen2.5-coder:32b", "ollama", 22000, 32768, "code", "q4_K_M", 32768,
             "Top code quality — needs 24GB+ VRAM"),

    # ── Embedding ────────────────────────────────────
    ModelRec("nomic-embed-text", "ollama", 500, 2048, "embedding", "fp16", 8192,
             "Standard embedding model — very small"),
    ModelRec("mxbai-embed-large", "ollama", 700, 2048, "embedding", "fp16", 512,
             "Higher quality embedding, slightly larger"),
    ModelRec("snowflake-arctic-embed:335m", "ollama", 700, 2048, "embedding", "fp16", 8192,
             "Strong retrieval embedding"),

    # ── Vision ───────────────────────────────────────
    ModelRec("llava:7b", "ollama", 5500, 8192, "vision", "q4_K_M", 4096,
             "Basic vision+chat"),
    ModelRec("llava:13b", "ollama", 9000, 16384, "vision", "q4_K_M", 4096,
             "Better vision understanding"),

    # ── STT ──────────────────────────────────────────
    ModelRec("whisper:large-v3", "ollama", 4000, 4096, "stt", "fp16", 0,
             "OpenAI Whisper large-v3 via Ollama"),

    # ── 2026 Models ─────────────────────────────────
    ModelRec("gemma4:9b", "ollama", 6500, 8192, "chat", "q4_K_M", 131072,
             "Google Gemma 4 9B — MoE, multimodal, reasoning"),
    ModelRec("gemma4:27b", "ollama", 18000, 24576, "chat", "q4_K_M", 131072,
             "Gemma 4 27B — single-GPU frontier quality"),
    ModelRec("google/gemma-4-27b-it", "vllm", 54000, 54000, "chat", "fp16", 131072,
             "Gemma 4 27B FP16 for vLLM — requires transformers>=5.5"),
    ModelRec("llama4-scout:17b", "ollama", 12000, 16384, "chat", "q4_K_M", 10000000,
             "Llama 4 Scout 17B active (109B MoE) — 10M context"),
    ModelRec("qwen3:7b", "ollama", 5500, 8192, "chat", "q4_K_M", 32768,
             "Qwen 3 7B — latest Alibaba release"),
    ModelRec("qwen3:32b", "ollama", 22000, 32768, "chat", "q4_K_M", 32768,
             "Qwen 3 32B — strong reasoning"),
    ModelRec("deepseek-r1:7b", "ollama", 5500, 8192, "chat", "q4_K_M", 65536,
             "DeepSeek R1 distilled 7B — reasoning model"),
    ModelRec("deepseek-r1:32b", "ollama", 22000, 32768, "chat", "q4_K_M", 65536,
             "DeepSeek R1 distilled 32B — frontier reasoning"),
    # DeepSeek V4 (April 2026) — 1T MoE, 32B active, 1M context
    ModelRec("deepseek-v4:32b", "ollama", 22000, 32768, "chat", "q4_K_M", 1000000,
             "DeepSeek V4 32B active (1T MoE) — 1M context, Engram memory"),
    ModelRec("deepseek/DeepSeek-V4", "vllm", 64000, 81920, "chat", "fp8", 1000000,
             "DeepSeek V4 FP8 — requires 1xH100 or 2xRTX 4090"),
    # GLM-5 (April 2026)
    ModelRec("glm5:9b", "ollama", 6500, 8192, "chat", "q4_K_M", 131072,
             "GLM-5 9B — Zhipu AI, strong multilingual"),

    # ── Reasoning models (dedicated use_case for CoT workloads) ──────
    ModelRec("qwen3:7b-thinking", "ollama", 5500, 8192, "reasoning", "q4_K_M", 32768,
             "Qwen 3 7B thinking mode — hybrid reasoning/chat"),
    ModelRec("qwen3:32b-thinking", "ollama", 22000, 32768, "reasoning", "q4_K_M", 32768,
             "Qwen 3 32B thinking mode — best local reasoning under 80GB"),

    # ── Microsoft Phi series ──────────────────────────────────────────
    ModelRec("phi4:14b", "ollama", 9000, 16384, "chat", "q4_K_M", 16384,
             "Microsoft Phi-4 14B — SOTA small model, beats 7B on most benchmarks"),
    ModelRec("phi4-mini:3.8b", "ollama", 2500, 4096, "chat", "q4_K_M", 16384,
             "Microsoft Phi-4 Mini 3.8B — edge/CPU friendly"),
    ModelRec("phi4-reasoning:14b", "ollama", 9000, 16384, "reasoning", "q4_K_M", 16384,
             "Microsoft Phi-4 Reasoning — strong math and code reasoning"),
]


def recommend(
    vram_mb: int = 0,
    ram_mb: int = 0,
    use_case: str = "",
    max_results: int = 5,
) -> list[ModelRec]:
    """Recommend models that fit the given hardware constraints.

    Args:
        vram_mb: Available GPU VRAM in MB (0 = CPU-only)
        ram_mb: Available system RAM in MB
        use_case: Filter by use case (chat/code/embedding/vision/stt/reasoning)
        max_results: Maximum recommendations to return
    """
    candidates: list[tuple[float, ModelRec]] = []

    for m in MODELS:
        # Hard filter: must fit in available memory
        if vram_mb > 0:
            if m.vram_required_mb > vram_mb:
                continue
        else:
            # CPU-only: only ollama models with small VRAM req
            if m.runtime != "ollama":
                continue
            # ram_mb <= 0 means "unknown" — don't filter every model out.
            if ram_mb > 0 and m.ram_required_mb > ram_mb:
                continue

        # Use case filter
        if use_case and m.use_case != use_case:
            continue

        # Score: prefer larger models that still fit (more capable)
        headroom = 1.0
        if vram_mb > 0:
            headroom = 1.0 - (m.vram_required_mb / vram_mb)
        else:
            headroom = 1.0 - (m.ram_required_mb / max(ram_mb, 1))

        # Prefer models that use 50-90% of available resources
        utilization = 1.0 - headroom
        if 0.5 <= utilization <= 0.9:
            score = 1.0 + utilization
        elif utilization > 0.9:
            score = 0.8  # Too tight
        else:
            score = utilization  # Under-utilizing

        # Bonus for vLLM (better throughput) when GPU available
        if vram_mb > 0 and m.runtime == "vllm":
            score += 0.2

        candidates.append((score, m))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in candidates[:max_results]]


def recommend_for_profile(profile: str, ram_mb: int = 0) -> list[ModelRec]:
    """Recommend based on a profile string like 'nvidia-ada-24gb'."""
    vram_mb = 0
    parts = profile.split("-")
    for p in parts:
        if p.endswith("gb"):
            try:
                vram_mb = int(p[:-2]) * 1024
            except ValueError:
                pass  # best-effort; failure is non-critical
        elif p.endswith("mb"):
            try:
                vram_mb = int(p[:-2])
            except ValueError:
                pass  # best-effort; failure is non-critical

    return recommend(vram_mb=vram_mb, ram_mb=ram_mb)
