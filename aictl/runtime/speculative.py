"""Speculative decoding configuration for vLLM and SGLang.

Speculative decoding achieves 1.3-2.5x speedup by using a small draft model
to predict tokens, then verifying them in parallel with the target model.

Methods (April 2026):
  EAGLE3:   Best quality. Requires trained draft head (~277MB).
            vLLM: --speculative-config '{"method":"eagle3","model":"..."}'
            SGLang: --speculative-algorithm EAGLE3

  P-EAGLE:  Parallel drafting variant. 1.05-1.69x over EAGLE3.
            vLLM: --speculative-config '{"method":"eagle3","parallel_drafting":true}'

  MTP:      Multi-Token Prediction. Model-native (DeepSeek, Qwen3).
            SGLang: --speculative-algorithm MTP

  NGRAM:    No draft model needed. GPU-based n-gram matching.
            vLLM: --speculative-config '{"method":"ngram","num_speculative_tokens":5}'
            SGLang: --speculative-algorithm NGRAM

  STANDALONE: Use any smaller model as draft.
            SGLang: --speculative-algorithm STANDALONE

References:
  - Red Hat: "Fly EAGLE3 fly" (2025-07)
  - Amazon: P-EAGLE (2026-03), 1.55x at c=1 on GPT-OSS-20B
  - ThoughtWorks: EAGLE3 on GLM-4.7-Flash (2026-04)
  - GPUStack: EAGLE3/MTP built-in support (2026)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SpeculativeConfig:
    method: str = "none"         # none | eagle3 | p-eagle | mtp | ngram | standalone
    draft_model: str = ""        # HF path for EAGLE3/STANDALONE draft model
    num_speculative_tokens: int = 5
    # EAGLE3-specific
    eagle_topk: int = 4          # Tree width for EAGLE3 drafting
    num_steps: int = 3           # Draft steps
    parallel_drafting: bool = False  # P-EAGLE mode
    # MTP-specific
    mtp_num_draft_tokens: int = 1
    # Performance
    draft_tensor_parallel: int = 1  # TP for draft model (usually 1)


# Known EAGLE3 draft models (April 2026)
EAGLE3_DRAFTS: dict[str, str] = {
    # Base model → EAGLE3 draft model
    "meta-llama/Llama-3.1-8B-Instruct": "yuhuili/EAGLE3-LLaMA3.1-Instruct-8B",
    "meta-llama/Llama-3.3-70B-Instruct": "yuhuili/EAGLE3-LLaMA3.3-Instruct-70B",
    "meta-llama/Meta-Llama-3.1-8B-Instruct": "jamesliu1/sglang-EAGLE3-Llama-3.1-Instruct-8B",
    "Qwen/Qwen2.5-7B-Instruct": "yuhuili/EAGLE3-Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-32B-Instruct": "yuhuili/EAGLE3-Qwen2.5-32B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct": "yuhuili/EAGLE3-Qwen2.5-72B-Instruct",
    "openai/gpt-oss-20b": "amazon/GPT-OSS-20B-P-EAGLE",
    "THUDM/GLM-4.7-Flash": "thoughtworks/GLM-4.7-Flash-Eagle3",
}

# Models with native MTP support
MTP_MODELS = {
    "deepseek-ai/DeepSeek-R1",
    "deepseek-ai/DeepSeek-V3",
    "Qwen/Qwen3-32B",
    "Qwen/Qwen3-235B-A22B",
}


def auto_select_method(model: str) -> SpeculativeConfig:
    """Auto-select the best speculative decoding method for a model."""
    # Check for native MTP
    if model in MTP_MODELS or any(m in model for m in ["DeepSeek-R1", "DeepSeek-V3", "Qwen3"]):
        return SpeculativeConfig(
            method="mtp",
            mtp_num_draft_tokens=1,
            num_speculative_tokens=3,
        )

    # Check for EAGLE3 draft model
    draft = EAGLE3_DRAFTS.get(model)
    if draft:
        # Use P-EAGLE if available (GPT-OSS models)
        parallel = "P-EAGLE" in draft or "p-eagle" in draft.lower()
        return SpeculativeConfig(
            method="eagle3",
            draft_model=draft,
            num_speculative_tokens=5 if parallel else 3,
            eagle_topk=4,
            num_steps=3,
            parallel_drafting=parallel,
        )

    # Fallback to NGRAM (no draft model needed)
    return SpeculativeConfig(
        method="ngram",
        num_speculative_tokens=5,
    )


def generate_vllm_args(config: SpeculativeConfig) -> list[str]:
    """Generate vLLM command-line arguments for speculative decoding."""
    if config.method == "none":
        return []

    if config.num_speculative_tokens <= 0:
        raise ValueError(f"num_speculative_tokens must be > 0, got {config.num_speculative_tokens}")

    if config.method in ("eagle3", "p-eagle"):
        if not config.draft_model:
            raise ValueError(f"draft_model is required for method={config.method}")
        spec_config: dict[str, Any] = {
            "method": "eagle3",
            "num_speculative_tokens": config.num_speculative_tokens,
        }
        if config.draft_model:
            spec_config["model"] = config.draft_model
        if config.parallel_drafting:
            spec_config["parallel_drafting"] = True
        if config.draft_tensor_parallel > 1:
            spec_config["draft_tensor_parallel_size"] = config.draft_tensor_parallel

        import json
        return [f"--speculative-config={json.dumps(spec_config)}"]

    if config.method == "ngram":
        import json
        spec_config = {
            "method": "ngram",
            "num_speculative_tokens": config.num_speculative_tokens,
        }
        return [f"--speculative-config={json.dumps(spec_config)}"]

    return []


def generate_sglang_args(config: SpeculativeConfig) -> list[str]:
    """Generate SGLang command-line arguments for speculative decoding."""
    if config.method == "none":
        return []

    if config.num_speculative_tokens <= 0:
        raise ValueError(f"num_speculative_tokens must be > 0, got {config.num_speculative_tokens}")

    args = []

    # SGLang's EAGLE3 algorithm covers both classic EAGLE-3 and the parallel
    # (p-eagle) variant; map both so an explicit p-eagle config still emits
    # speculative flags instead of silently returning an empty list.
    if config.method in ("eagle3", "p-eagle"):
        if not config.draft_model:
            raise ValueError(f"draft_model is required for method={config.method}")
        args.append("--speculative-algorithm=EAGLE3")
        if config.draft_model:
            args.append(f"--speculative-draft-model-path={config.draft_model}")
        args.append(f"--speculative-num-steps={config.num_steps}")
        args.append(f"--speculative-eagle-topk={config.eagle_topk}")
        args.append(f"--speculative-num-draft-tokens={config.num_speculative_tokens}")

    elif config.method == "mtp":
        args.append("--speculative-algorithm=MTP")
        args.append(f"--speculative-num-draft-tokens={config.mtp_num_draft_tokens}")

    elif config.method == "ngram":
        args.append("--speculative-algorithm=NGRAM")
        args.append(f"--speculative-num-draft-tokens={config.num_speculative_tokens}")

    elif config.method == "standalone":
        args.append("--speculative-algorithm=STANDALONE")
        if config.draft_model:
            args.append(f"--speculative-draft-model-path={config.draft_model}")

    return args


def estimate_speedup(config: SpeculativeConfig) -> dict[str, Any]:
    """Estimate speedup from speculative decoding configuration."""
    speedups = {
        "eagle3": {"latency": 1.3, "throughput": 1.7, "note": "EAGLE3 draft head"},
        "p-eagle": {"latency": 1.5, "throughput": 2.1, "note": "P-EAGLE parallel drafting"},
        "mtp": {"latency": 1.2, "throughput": 1.4, "note": "Native MTP (DeepSeek/Qwen3)"},
        "ngram": {"latency": 1.1, "throughput": 1.2, "note": "GPU N-gram matching, no extra model"},
        "none": {"latency": 1.0, "throughput": 1.0, "note": "Standard autoregressive"},
    }

    method = config.method
    if config.parallel_drafting and method == "eagle3":
        method = "p-eagle"

    base = speedups.get(method, speedups["none"])
    return {
        "method": method,
        "estimated_latency_speedup": base["latency"],
        "estimated_throughput_speedup": base["throughput"],
        "draft_model": config.draft_model or "(none)",
        "note": base["note"],
    }
