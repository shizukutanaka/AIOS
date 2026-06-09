"""vLLM optimization flag generator.

Auto-generates optimal vLLM serve flags for a given model + hardware combo.
Covers all production-critical flags from vLLM v0.19:

  Memory:
    --kv-cache-dtype fp8         # 2x KV cache compression (Hopper+)
    --gpu-memory-utilization 0.9 # VRAM fraction
    --max-model-len 32768        # Context window
    --enable-prefix-caching      # Share prefixes across requests
    --enable-chunked-prefill     # Prevent HoL blocking

  Performance:
    --performance-mode balanced|interactivity|throughput
    --dtype auto                 # auto-detect FP16/BF16/FP8
    --enforce-eager              # Disable CUDA graphs (debug only)

  Speculative:
    --speculative-config {...}   # EAGLE3/MTP/NGRAM

  Serving:
    --v1                         # Use V1 engine (async scheduler)
    --port 8000
    --served-model-name <alias>
    --grpc                       # Enable gRPC (v0.19+)

  Distributed:
    --tensor-parallel-size N     # TP across GPUs
    --pipeline-parallel-size N   # PP across nodes
    --kv-transfer-config {...}   # P/D disaggregation
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HardwareProfile:
    gpu_name: str = ""
    gpu_count: int = 1
    vram_per_gpu_mb: int = 0
    compute_capability: int = 0   # e.g. 89=Ada, 90=Hopper, 100=Blackwell
    has_nvlink: bool = False


@dataclass
class OptimizeResult:
    model: str
    flags: list[str]
    estimated_vram_mb: int = 0
    estimated_throughput_tps: int = 0
    kv_cache_dtype: str = "auto"
    performance_mode: str = "balanced"
    tensor_parallel: int = 1
    notes: list[str] = field(default_factory=list)


# GPU compute capability map
GPU_CC: dict[str, int] = {
    "RTX 3090": 86, "RTX 4090": 89, "RTX 5090": 120,
    "A100": 80, "H100": 90, "H200": 90,
    "B200": 100, "GB200": 100,
    "L40S": 89, "L4": 89,
}


def optimize_vllm_flags(
    model: str,
    model_size_b: float,    # Billions of parameters
    hardware: HardwareProfile,
    max_context: int = 0,
    objective: str = "balanced",  # balanced | throughput | interactivity
    enable_speculative: bool = False,
) -> OptimizeResult:
    """Generate optimal vLLM flags for model + hardware."""
    flags: list[str] = []
    notes: list[str] = []

    # ── Model + basic flags ──
    flags.extend(["--model", model, "--v1"])

    # ── Performance mode ──
    flags.append(f"--performance-mode={objective}")

    # ── VRAM calculation ──
    bytes_per_param_fp16 = 2
    model_vram_mb = int(model_size_b * 1e9 * bytes_per_param_fp16 / 1e6)
    total_vram = hardware.vram_per_gpu_mb * hardware.gpu_count

    # ── Tensor parallelism ──
    tp = 1
    if model_vram_mb > hardware.vram_per_gpu_mb * 0.85 and hardware.gpu_count > 1:
        tp = min(hardware.gpu_count, _smallest_power_of_2_gte(
            model_vram_mb / (hardware.vram_per_gpu_mb * 0.7)
        ))
        flags.append(f"--tensor-parallel-size={tp}")
        notes.append(f"TP={tp} needed: model {model_vram_mb}MB > single GPU {hardware.vram_per_gpu_mb}MB")

    # ── KV cache dtype ──
    cc = hardware.compute_capability or GPU_CC.get(hardware.gpu_name, 0)
    kv_dtype = "auto"
    if cc >= 89:  # Ada Lovelace or newer
        kv_dtype = "fp8"
        flags.append("--kv-cache-dtype=fp8")
        notes.append("FP8 KV cache: 2x compression (GPU CC >= 8.9)")
    elif cc >= 80:
        kv_dtype = "fp8_e5m2"
        flags.append("--kv-cache-dtype=fp8_e5m2")
        notes.append("FP8 E5M2 KV cache: lower precision but fits on A100")

    # ── GPU memory utilization ──
    gpu_util = 0.90
    if tp > 1:
        gpu_util = 0.85  # Leave headroom for TP communication
    flags.append(f"--gpu-memory-utilization={gpu_util}")

    # ── Context length ──
    if max_context > 0:
        flags.append(f"--max-model-len={max_context}")
    else:
        # Auto-detect reasonable context for VRAM. total_vram is the aggregate
        # across all GPUs and, with TP, the model is sharded — so the whole
        # model footprint (model_vram_mb) is subtracted once, not /tp.
        # FP8 KV cache gives 2x compression → same VRAM holds 2x more tokens.
        available_for_kv = (total_vram * gpu_util - model_vram_mb) * (2.0 if kv_dtype == "fp8" else 1.0)
        # Rough: 1MB per 1K context tokens for 8B model. Guard div-by-zero.
        context_budget = max(4096, min(131072,
                                       int(available_for_kv / max(model_size_b, 1) * 1000)))
        flags.append(f"--max-model-len={context_budget}")

    # ── Prefix caching ──
    flags.append("--enable-prefix-caching")
    notes.append("Prefix caching: reuse KV cache for shared prompts")

    # ── Chunked prefill ──
    flags.append("--enable-chunked-prefill")
    notes.append("Chunked prefill: prevents head-of-line blocking")

    # ── Dtype ──
    if cc >= 100:  # Blackwell
        flags.append("--dtype=fp8")
        notes.append("FP8 weights: native on Blackwell (4x throughput vs FP16)")
    elif cc >= 89:  # Ada
        flags.append("--dtype=auto")
    else:
        flags.append("--dtype=auto")

    # ── Speculative decoding ──
    if enable_speculative and model_size_b >= 7:
        from aictl.runtime.speculative import auto_select_method, generate_vllm_args
        spec_config = auto_select_method(model)
        if spec_config:
            spec_flags = generate_vllm_args(spec_config)
            flags.extend(spec_flags)
            notes.append(f"Speculative decoding: {spec_config.method}")

    # ── Structured output (xgrammar default in vLLM v0.19) ──
    notes.append("Structured output: xgrammar backend (auto), supports guided_json/response_format")
    if objective == "throughput":
        notes.append("  Tip: Cache JSON schemas — first request compiles FSM (~50-200ms), reuse is free")

    # ── Throughput estimate ──
    base_tps = 0
    if hardware.gpu_name in GPU_CC:
        # Rough per-GPU estimate for 8B model
        base_rates = {"RTX 4090": 80, "A100": 130, "H100": 280, "H200": 450, "B200": 700, "GB200": 900}
        base = base_rates.get(hardware.gpu_name, 100)
        base_tps = int(base * (8.0 / max(model_size_b, 1)) * tp)

    return OptimizeResult(
        model=model,
        flags=flags,
        estimated_vram_mb=model_vram_mb,
        estimated_throughput_tps=base_tps,
        kv_cache_dtype=kv_dtype,
        performance_mode=objective,
        tensor_parallel=tp,
        notes=notes,
    )


def flags_to_command(result: OptimizeResult, port: int = 8000) -> str:
    """Convert optimization result to a runnable vllm serve command."""
    args = " \\\n    ".join(result.flags + [f"--port={port}"])
    return f"vllm serve \\\n    {args}"


def _smallest_power_of_2_gte(n: float) -> int:
    """Smallest power of 2 >= n."""
    p = 1
    while p < n:
        p *= 2
    return p
