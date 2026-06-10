"""llm-d P/D Disaggregation manifest generator.

llm-d (CNCF Sandbox, accepted 2026-03-24) is the standard for
Kubernetes-native disaggregated LLM inference.

Key architecture:
  Prefill instance: compute-bound, processes full input prompt
  Decode instance: memory-bandwidth-bound, generates tokens one-at-a-time
  KV connector: transfers KV cache from prefill → decode (NIXL or LMCache)

Companies using in production: Meta, LinkedIn, Hugging Face, Mistral AI.

Usage:
  aictl deploy disagg <model> --prefill-replicas 2 --decode-replicas 4
"""

from __future__ import annotations

from dataclasses import dataclass
from aictl.core.constants import VLLM_IMAGE, DEFAULT_MAX_MODEL_LEN, DEFAULT_GPU_MEMORY_UTIL, VLLM_DEFAULT_PORT
from typing import Any


@dataclass
class DisaggConfig:
    """Configuration for P/D disaggregated deployment."""
    model: str                       # HuggingFace model ID
    prefill_replicas: int = 1
    decode_replicas: int = 2         # Typically 2-4x prefill
    prefill_gpu: int = 1
    decode_gpu: int = 1
    gpu_memory_utilization: float = DEFAULT_GPU_MEMORY_UTIL
    max_model_len: int = DEFAULT_MAX_MODEL_LEN
    kv_connector: str = "NixlConnector"  # NixlConnector | LMCacheConnector
    nixl_buffer_size: int = 1073741824   # 1GB
    enable_prefix_caching: bool = True
    enable_chunked_prefill: bool = True
    image: str = VLLM_IMAGE
    namespace: str = "default"
    port: int = VLLM_DEFAULT_PORT


def generate_disagg_manifests(config: DisaggConfig) -> list[dict[str, Any]]:
    """Generate K8s manifests for P/D disaggregated inference.

    Generates:
      1. Prefill Deployment + Service
      2. Decode Deployment + Service
      3. Gateway API InferencePool + InferenceModel
      4. llm-d ModelService CRD (if available)
    """
    model_slug = (config.model.rstrip("/").split("/")[-1] or "model").lower().replace(".", "-")
    resources: list[dict[str, Any]] = []

    # ── Prefill Deployment ──
    prefill_args = [
        "--model", config.model,
        "--gpu-memory-utilization", str(config.gpu_memory_utilization),
        "--max-model-len", str(config.max_model_len),
        "--kv-transfer-config", _kv_config("kv_producer", config),
        "--v1",
    ]
    if config.enable_prefix_caching:
        prefill_args.append("--enable-prefix-caching")
    if config.enable_chunked_prefill:
        prefill_args.append("--enable-chunked-prefill")

    resources.append(_deployment(
        name=f"{model_slug}-prefill",
        image=config.image,
        args=prefill_args,
        replicas=config.prefill_replicas,
        gpu=config.prefill_gpu,
        namespace=config.namespace,
        port=config.port,
        labels={"role": "prefill", "model": model_slug},
    ))
    resources.append(_service(
        f"{model_slug}-prefill", config.namespace, config.port,
        {"role": "prefill", "model": model_slug},
    ))

    # ── Decode Deployment ──
    decode_args = [
        "--model", config.model,
        "--gpu-memory-utilization", str(config.gpu_memory_utilization),
        "--max-model-len", str(config.max_model_len),
        "--kv-transfer-config", _kv_config("kv_consumer", config),
        "--v1",
    ]
    if config.enable_prefix_caching:
        decode_args.append("--enable-prefix-caching")

    resources.append(_deployment(
        name=f"{model_slug}-decode",
        image=config.image,
        args=decode_args,
        replicas=config.decode_replicas,
        gpu=config.decode_gpu,
        namespace=config.namespace,
        port=config.port,
        labels={"role": "decode", "model": model_slug},
    ))
    resources.append(_service(
        f"{model_slug}-decode", config.namespace, config.port,
        {"role": "decode", "model": model_slug},
    ))

    # ── Gateway API InferencePool (decode endpoint) ──
    resources.append({
        "apiVersion": "inference.networking.k8s.io/v1",
        "kind": "InferencePool",
        "metadata": {
            "name": f"{model_slug}-pool",
            "namespace": config.namespace,
        },
        "spec": {
            "targetPorts": [{"number": config.port}],
            "selector": {"role": "decode", "model": model_slug},
            "extensionRef": {
                "name": f"{model_slug}-epp",
                "port": 9002,
                "failureMode": "FailOpen",
            },
        },
    })

    # ── InferenceModel ──
    resources.append({
        "apiVersion": "inference.networking.k8s.io/v1",
        "kind": "InferenceModel",
        "metadata": {
            "name": f"{model_slug}-model",
            "namespace": config.namespace,
        },
        "spec": {
            "modelName": config.model,
            "criticality": "Critical",
            "poolRef": {"name": f"{model_slug}-pool"},
        },
    })

    return resources


def _kv_config(role: str, config: DisaggConfig) -> str:
    """Generate --kv-transfer-config JSON string."""
    import json
    cfg: dict[str, Any] = {
        "kv_connector": config.kv_connector,
        "kv_role": role,
    }
    if config.kv_connector == "NixlConnector":
        cfg["kv_connector_extra_config"] = {
            "buffer_size": config.nixl_buffer_size,
            "buffer_device": "cuda",
            "enable_gc": True,
        }
    return json.dumps(cfg)


def _deployment(name: Any, image: Any, args: list[str], replicas: Any, gpu: Any, namespace: Any, port: Any, labels: Any) -> dict[str, Any]:
    """Generate Kubernetes Deployment manifest."""
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [{
                        "name": "vllm",
                        "image": image,
                        "args": args,
                        "ports": [{"containerPort": port}],
                        "resources": {
                            "limits": {"nvidia.com/gpu": str(gpu)},
                            "requests": {"nvidia.com/gpu": str(gpu)},
                        },
                        "readinessProbe": {
                            "httpGet": {"path": "/health", "port": port},
                            "initialDelaySeconds": 60,
                            "periodSeconds": 10,
                        },
                        "livenessProbe": {
                            "httpGet": {"path": "/health", "port": port},
                            "initialDelaySeconds": 120,
                            "periodSeconds": 30,
                        },
                    }],
                    "tolerations": [
                        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"},
                    ],
                },
            },
        },
    }


def _service(name: Any, namespace: Any, port: Any, selector: Any) -> dict[str, Any]:
    """Generate Kubernetes Service manifest."""
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": f"{name}-svc", "namespace": namespace},
        "spec": {
            "selector": selector,
            "ports": [{"port": port, "targetPort": port, "protocol": "TCP"}],
            "type": "ClusterIP",
        },
    }
