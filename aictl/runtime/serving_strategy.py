"""Serving-strategy advisor: P/D-disagg vs chunked-prefill-aggregation vs AFD.

aictl already *generates* P/D-disaggregated manifests (`stack/disagg.py`) and
KVBM configs (`runtime/dynamo.py`), but never advised *which serving topology*
to pick. This module fills backlog item **F**: given model type (dense vs MoE),
GPU count, and a latency-vs-throughput objective, it recommends one of three
2025–26 serving strategies and emits ready-to-paste flags.

The three strategies (and when each wins):

  aggregated   — one instance does prefill+decode, with **chunked prefill** so
                 long prompts don't stall decode. Simplest; best for small/
                 medium dense models and low GPU counts. Chunked-prefill +
                 EP aggregation now beats naive P/D for throughput at small
                 scale (arXiv:2506.05508).
  pd-disagg    — **prefill/decode disaggregation**: separate compute-bound
                 prefill pods from bandwidth-bound decode pods, KV cache moved
                 over NIXL/LMCache. Wins for large dense models at scale and
                 throughput-first SLAs (Mooncake/DeepSeek-V3 pattern).
  afd          — **Attention–FFN disaggregation**: split attention from FFN/
                 expert layers, the latency-winning topology for **MoE** models
                 (arXiv:2605.28302). Needs many GPUs and expert parallelism.

This is an advisor, not a runtime — it recommends and prints flags; existing
`deploy disagg` / `deploy optimize` generate the actual manifests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Known Mixture-of-Experts model families (substring match, case-insensitive).
_MOE_HINTS = (
    "mixtral", "deepseek-v3", "deepseek-v2", "deepseek-r1", "qwen3-moe",
    "qwen2-moe", "qwen1.5-moe", "dbrx", "grok", "arctic", "jamba",
    "phi-3.5-moe", "olmoe", "minimax", "scout", "maverick", "-moe",
)

VALID_OBJECTIVES = ("balanced", "latency", "throughput")


@dataclass
class StrategyRecommendation:
    """A serving-topology recommendation with rationale and flags."""
    strategy: str                  # aggregated | pd-disagg | afd
    model_type: str                # dense | moe
    objective: str
    gpu_count: int
    rationale: str
    vllm_flags: list[str] = field(default_factory=list)
    next_command: str = ""         # the aictl command to materialize it
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for --json output."""
        return {
            "strategy": self.strategy,
            "model_type": self.model_type,
            "objective": self.objective,
            "gpu_count": self.gpu_count,
            "rationale": self.rationale,
            "vllm_flags": self.vllm_flags,
            "next_command": self.next_command,
            "references": self.references,
        }


def detect_model_type(model: str) -> str:
    """Classify a model name as 'moe' or 'dense' from known family hints."""
    lo = model.lower()
    return "moe" if any(h in lo for h in _MOE_HINTS) else "dense"


def recommend_strategy(model: str, gpu_count: int = 1,
                       objective: str = "balanced",
                       model_type: str | None = None) -> StrategyRecommendation:
    """Recommend a serving topology for a model + GPU budget + objective.

    Decision logic (advisory, grounded in 2025–26 disaggregation research):
      * MoE + latency-leaning + enough GPUs (≥4)   → AFD
      * dense + throughput/scale + enough GPUs (≥2) → P/D disaggregation
      * everything else (small scale, balanced)     → aggregated + chunked prefill
    """
    mt = (model_type or detect_model_type(model)).lower()
    obj = objective if objective in VALID_OBJECTIVES else "balanced"
    n = max(1, gpu_count)

    # ── AFD: MoE, latency-sensitive, multi-GPU ──
    if mt == "moe" and obj in ("latency", "balanced") and n >= 4:
        return StrategyRecommendation(
            strategy="afd", model_type=mt, objective=obj, gpu_count=n,
            rationale=(
                "MoE model with ≥4 GPUs and a latency-leaning objective: "
                "Attention–FFN disaggregation lets attention and expert/FFN "
                "layers scale independently, the latency-winning topology for "
                "MoE (arXiv:2605.28302). Use expert parallelism for the FFN tier."),
            vllm_flags=[
                "--enable-expert-parallel",
                f"--tensor-parallel-size {min(n, 8)}",
                "--enable-chunked-prefill",
                "--enable-prefix-caching",
            ],
            next_command=f"aictl deploy disagg {model} --prefill-replicas 2 --decode-replicas {max(2, n - 2)}",
            references=["arXiv:2605.28302 (Attention–FFN disaggregation)",
                        "arXiv:2506.05508 (disaggregation trade-offs)"],
        )

    # ── P/D disaggregation: dense at scale or throughput-first ──
    if n >= 2 and (obj == "throughput" or (mt == "dense" and n >= 4)):
        prefill = max(1, n // 3)
        decode = max(1, n - prefill)
        return StrategyRecommendation(
            strategy="pd-disagg", model_type=mt, objective=obj, gpu_count=n,
            rationale=(
                "Multi-GPU and throughput/scale-oriented: separate compute-bound "
                "prefill from bandwidth-bound decode so each scales to its own "
                "bottleneck, with KV cache moved over NIXL/LMCache "
                "(Mooncake/DeepSeek-V3 pattern). Typical decode:prefill ≈ 2:1."),
            vllm_flags=[
                "--enable-chunked-prefill",
                "--enable-prefix-caching",
                "--kv-transfer-config '{\"kv_connector\":\"NixlConnector\"}'",
            ],
            next_command=(f"aictl deploy disagg {model} "
                          f"--prefill-replicas {prefill} --decode-replicas {decode}"),
            references=["arXiv:2506.05508 (P/D vs chunked-prefill aggregation)",
                        "Mooncake / DeepSeek-V3 disaggregation"],
        )

    # ── Aggregated + chunked prefill: small/medium, balanced ──
    return StrategyRecommendation(
        strategy="aggregated", model_type=mt, objective=obj, gpu_count=n,
        rationale=(
            "Small/medium scale or balanced objective: a single instance doing "
            "prefill+decode with chunked prefill avoids decode stalls on long "
            "prompts and beats naive P/D at this scale (arXiv:2506.05508). "
            "Simplest to operate; add EP aggregation only if it's MoE."),
        vllm_flags=(
            ["--enable-chunked-prefill", "--enable-prefix-caching"]
            + (["--enable-expert-parallel"] if mt == "moe" else [])
            + ([f"--tensor-parallel-size {n}"] if n > 1 else [])
        ),
        next_command=f"aictl deploy optimize {model} --gpu-count {n} --objective {obj}",
        references=["arXiv:2506.05508 (chunked-prefill aggregation)"],
    )


def strategy_matrix(model: str = "<model>") -> list[dict[str, Any]]:
    """A small comparison matrix of all three strategies for help/overview."""
    return [
        {"strategy": "aggregated", "best_for": "small/medium dense, low GPU count, balanced",
         "key_flag": "--enable-chunked-prefill"},
        {"strategy": "pd-disagg", "best_for": "large dense at scale, throughput-first",
         "key_flag": "--kv-transfer-config (NIXL/LMCache)"},
        {"strategy": "afd", "best_for": "MoE models, latency-sensitive, ≥4 GPUs",
         "key_flag": "--enable-expert-parallel"},
    ]
