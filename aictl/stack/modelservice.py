"""llm-d ModelService Helm values generator.

llm-d (CNCF Sandbox, 2026-03-24) v0.5 uses ModelService Helm charts
instead of the deprecated CRD operator. This module generates
values.yaml for deploying models via Helm.

ModelService manages:
  - vLLM pod lifecycle (Deployment + Service)
  - Gateway API InferencePool/InferenceModel
  - LeaderWorkerSet for multi-GPU models
  - Preset configs (latency-optimized, throughput-optimized, etc.)

Usage:
  aictl deploy modelservice <model> > values.yaml
  helm install my-model oci://ghcr.io/llm-d/charts/modelservice -f values.yaml

Benchmarks (v0.5):
  - 3,100 tok/s per B200 decode GPU
  - 50,000 tok/s on 16×16 B200 (prefill/decode)
  - 40% TPOT reduction on DeepSeek V3.1 (H200)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aictl.core.constants import VLLM_IMAGE


@dataclass
class ModelServiceConfig:
    model: str                        # HuggingFace model ID
    preset: str = "balanced"          # balanced | latency | throughput
    replicas: int = 1
    gpu_count: int = 1
    gpu_type: str = "nvidia.com/gpu"  # nvidia.com/gpu | amd.com/gpu
    tensor_parallel: int = 1
    max_model_len: int = 32768
    kv_cache_dtype: str = "auto"
    enable_prefix_caching: bool = True
    enable_lora: bool = False
    lora_adapters: list[str] = field(default_factory=list)
    namespace: str = "default"
    image: str = VLLM_IMAGE


# Preset configurations matching llm-d's BaseConfig patterns
PRESETS: dict[str, dict[str, Any]] = {
    "balanced": {
        "performanceMode": "balanced",
        "gpuMemoryUtilization": 0.9,
        "enablePrefixCaching": True,
        "enableChunkedPrefill": True,
        "kvCacheDtype": "fp8",
    },
    "latency": {
        "performanceMode": "interactivity",
        "gpuMemoryUtilization": 0.85,
        "enablePrefixCaching": True,
        "enableChunkedPrefill": True,
        "kvCacheDtype": "fp8",
        "maxBatchSize": 32,
    },
    "throughput": {
        "performanceMode": "throughput",
        "gpuMemoryUtilization": 0.95,
        "enablePrefixCaching": True,
        "enableChunkedPrefill": True,
        "kvCacheDtype": "fp8",
        "maxBatchSize": 256,
    },
}


def generate_helm_values(config: ModelServiceConfig) -> dict[str, Any]:
    """Generate Helm values.yaml for llm-d ModelService."""
    preset = PRESETS.get(config.preset, PRESETS["balanced"])
    model_slug = config.model.split("/")[-1].lower().replace(".", "-")

    values: dict[str, Any] = {
        "modelService": {
            "name": model_slug,
            "model": config.model,
            "namespace": config.namespace,
        },
        "servingEngine": {
            "image": config.image,
            "replicaCount": config.replicas,
            "containerPort": 8000,
            "resources": {
                "limits": {config.gpu_type: config.gpu_count},
                "requests": {config.gpu_type: config.gpu_count},
            },
        },
        "vllmConfig": {
            "model": config.model,
            "tensorParallelSize": config.tensor_parallel,
            "maxModelLen": config.max_model_len,
            "gpuMemoryUtilization": preset["gpuMemoryUtilization"],
            "performanceMode": preset["performanceMode"],
            "enablePrefixCaching": preset["enablePrefixCaching"],
            "enableChunkedPrefill": preset["enableChunkedPrefill"],
            "kvCacheDtype": config.kv_cache_dtype if config.kv_cache_dtype != "auto" else preset.get("kvCacheDtype", "auto"),
            "v1": True,
            "dtype": "auto",
        },
        "inferencePool": {
            "enabled": True,
            "name": f"{model_slug}-pool",
            "targetPort": 8000,
            "eppPort": 9002,
            "failureMode": "FailOpen",
        },
        "inferenceModel": {
            "enabled": True,
            "name": f"{model_slug}-model",
            "modelName": config.model,
            "criticality": "Critical",
        },
        "autoscaling": {
            "enabled": True,
            "minReplicas": max(1, config.replicas),
            # Never below minReplicas, even when config.replicas is 0/1.
            "maxReplicas": max(config.replicas * 4, max(1, config.replicas)),
            "scaleToZero": False,
            "targetMetric": "queue_depth",
            "targetValue": 5,
        },
        "monitoring": {
            "prometheus": True,
            "serviceMonitor": True,
        },
    }

    # LoRA configuration
    if config.enable_lora and config.lora_adapters:
        values["vllmConfig"]["enableLora"] = True
        values["vllmConfig"]["maxLoras"] = len(config.lora_adapters)
        values["vllmConfig"]["loraModules"] = config.lora_adapters

    # Multi-GPU: LeaderWorkerSet
    if config.tensor_parallel > 1:
        values["leaderWorkerSet"] = {
            "enabled": True,
            "size": config.tensor_parallel,
        }

    return values


def values_to_yaml(values: dict[str, Any], indent: int = 0) -> str:
    """Convert dict to YAML string (simple, no pyyaml dependency)."""
    lines: list[str] = []
    prefix = "  " * indent
    for k, v in values.items():
        if isinstance(v, dict):
            lines.append(f"{prefix}{k}:")
            lines.append(values_to_yaml(v, indent + 1))
        elif isinstance(v, list):
            lines.append(f"{prefix}{k}:")
            for item in v:
                lines.append(f"{prefix}  - {item}")
        elif isinstance(v, bool):
            lines.append(f"{prefix}{k}: {'true' if v else 'false'}")
        elif isinstance(v, str):
            lines.append(f'{prefix}{k}: "{v}"')
        else:
            lines.append(f"{prefix}{k}: {v}")
    return "\n".join(lines)
