"""K8s Gateway API Inference Extension: InferencePool + InferenceModel.

The Gateway API Inference Extension (2026) is the official K8s standard
for routing to AI inference workloads. It introduces:

  InferencePool (v1, stable):
    Group of model server pods with an Endpoint Picker Extension (EPP).
    Routes based on KV-cache utilization, queue depth, active LoRA adapters.
    Supported by: Istio v1.28, NGINX GW Fabric v2.5, kgateway v2.0, GKE.

  InferenceModel:
    Maps a public model name to actual backend models in an InferencePool.
    Criticality levels: Critical | Standard | Sheddable.

  Endpoint Picker (EPP):
    Monitors per-pod metrics, picks optimal endpoint.
    Uses Envoy ext-proc for routing decisions.

Usage:
  aictl cluster gateway <stack>    # Generate Gateway + InferencePool + InferenceModel
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aictl.stack.manifest import StackManifest, ServiceDef


@dataclass
class GatewayInferenceConfig:
    namespace: str = "default"
    gateway_class: str = "istio"  # istio | nginx | kgateway | gke
    gateway_name: str = "inference-gateway"
    port: int = 80
    epp_port: int = 9002


def stack_to_gateway_api(
    manifest: StackManifest,
    config: GatewayInferenceConfig | None = None,
) -> list[dict[str, Any]]:
    """Convert a Stack manifest to Gateway API Inference Extension resources.

    Generates: Gateway + HTTPRoute + InferencePool + InferenceModel per service.
    """
    if config is None:
        config = GatewayInferenceConfig()

    resources: list[dict[str, Any]] = []
    pool_names: list[str] = []

    # Generate InferencePool + InferenceModel for each inference service
    for svc in manifest.services:
        if not _is_inference_service(svc):
            continue

        pool_name = f"{manifest.name}-{svc.name}"
        pool_names.append(pool_name)

        # InferencePool
        pool: dict[str, Any] = {
            "apiVersion": "inference.networking.k8s.io/v1",
            "kind": "InferencePool",
            "metadata": {
                "name": pool_name,
                "namespace": config.namespace,
                "labels": {"aios.stack": manifest.name},
            },
            "spec": {
                "targetPorts": [{"number": svc.port or 8000}],
                # Must match the pod label the workloads actually carry
                # (kserve/orchestrator emit `aios.service`, not `app`).
                "selector": {"aios.service": pool_name},
                "extensionRef": {
                    "name": f"{pool_name}-epp",
                    "port": config.epp_port,
                    "failureMode": "FailOpen",
                },
            },
        }
        resources.append(pool)

        # InferenceModel
        model_name = svc.model or svc.name
        criticality = "Critical" if svc.gpu_required else "Standard"

        inference_model: dict[str, Any] = {
            "apiVersion": "inference.networking.k8s.io/v1",
            "kind": "InferenceModel",
            "metadata": {
                "name": f"{pool_name}-model",
                "namespace": config.namespace,
            },
            "spec": {
                "modelName": model_name,
                "criticality": criticality,
                "poolRef": {"name": pool_name},
            },
        }

        # LoRA / version routing
        if hasattr(svc, "target_models") and svc.target_models:
            inference_model["spec"]["targetModels"] = svc.target_models
        else:
            inference_model["spec"]["targetModels"] = [
                {"name": model_name, "weight": 100},
            ]

        resources.append(inference_model)

    # Gateway
    gateway: dict[str, Any] = {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "Gateway",
        "metadata": {
            "name": config.gateway_name,
            "namespace": config.namespace,
        },
        "spec": {
            "gatewayClassName": config.gateway_class,
            "listeners": [{
                "name": "http",
                "protocol": "HTTP",
                "port": config.port,
                "allowedRoutes": {"namespaces": {"from": "Same"}},
            }],
        },
    }
    resources.append(gateway)

    # HTTPRoute
    if pool_names:
        backends = [
            {
                "kind": "InferencePool",
                "group": "inference.networking.k8s.io",
                "name": name,
            }
            for name in pool_names
        ]

        route: dict[str, Any] = {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {
                "name": f"{manifest.name}-route",
                "namespace": config.namespace,
            },
            "spec": {
                "parentRefs": [{"name": config.gateway_name}],
                "rules": [{
                    "matches": [{"path": {"type": "PathPrefix", "value": "/v1"}}],
                    "backendRefs": backends,
                }],
            },
        }
        resources.append(route)

    return resources


def _is_inference_service(svc: ServiceDef) -> bool:
    """Check if a service is an inference engine (vs. UI, retriever, etc.)."""
    if svc.runtime in ("vllm", "sglang", "ollama", "trt-llm"):
        return True
    if svc.model:
        return True
    return False
