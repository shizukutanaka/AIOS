"""KServe LLMInferenceService manifest generator.

Converts aictl Stack manifests to KServe v0.17 LLMInferenceService CRDs
with llm-d integration for KV-cache aware scheduling and P/D disaggregation.

Based on research (April 2026):
  - KServe v0.17 with LLMInferenceService CRD
  - llm-d for distributed scheduling (KV-cache locality, P/D split)
  - Gateway API + GIE for routing
  - vLLM v0.19 as primary runtime (--performance-mode support)
"""

from __future__ import annotations
from aictl.core.constants import VLLM_IMAGE

from dataclasses import dataclass
from typing import Any

from aictl.stack.manifest import StackManifest, ServiceDef


RUNTIME_IMAGES = {
    "vllm": VLLM_IMAGE,
    "sglang": "lmsysorg/sglang:latest",
    "ollama": "docker.io/ollama/ollama:v0.20.4",
}

VLLM_PERFORMANCE_MODES = ("balanced", "interactivity", "throughput")


@dataclass
class LLMISvcConfig:
    replicas: int = 1
    tensor_parallel: int = 1
    pipeline_parallel: int = 1
    performance_mode: str = "balanced"
    enable_prefix_caching: bool = True
    enable_pd_disagg: bool = False
    kv_cache_type: str = "auto"
    max_model_len: int = 0
    gpu_memory_utilization: float = 0.9
    speculative_model: str = ""
    speculative_tokens: int = 0


def stack_to_llmisvc(
    manifest: StackManifest,
    config: LLMISvcConfig | None = None,
    namespace: str = "default",
) -> list[dict[str, Any]]:
    """Convert a Stack to KServe LLMInferenceService manifests."""
    if config is None:
        config = LLMISvcConfig()

    resources: list[dict[str, Any]] = []

    for svc in manifest.services:
        if svc.runtime not in ("vllm", "sglang"):
            # Non-LLM services become regular Deployments
            resources.extend(_to_deployment(svc, manifest.name, namespace))
            continue

        # LLMInferenceService
        llmisvc = _build_llmisvc(svc, manifest.name, namespace, config)
        resources.append(llmisvc)

    return resources


def _build_llmisvc(svc: ServiceDef, stack_name: str, namespace: str,
                   config: LLMISvcConfig) -> dict[str, Any]:
    """Build a KServe LLMInferenceService CRD."""
    name = f"aios-{stack_name}-{svc.name}"
    image = RUNTIME_IMAGES.get(svc.runtime, svc.image)

    # Container args for vLLM
    args: list[str] = []
    if svc.runtime == "vllm":
        if config.performance_mode in VLLM_PERFORMANCE_MODES:
            args.extend(["--performance-mode", config.performance_mode])
        if config.enable_prefix_caching:
            args.append("--enable-prefix-caching")
        if config.gpu_memory_utilization != 0.9:
            args.extend(["--gpu-memory-utilization", str(config.gpu_memory_utilization)])
        if config.max_model_len > 0:
            args.extend(["--max-model-len", str(config.max_model_len)])
        if config.speculative_model:
            spec_config = {
                "model": config.speculative_model,
                "num_speculative_tokens": config.speculative_tokens or 3,
            }
            import json
            args.extend(["--speculative-config", json.dumps(spec_config)])

    # GPU resources
    gpu_count = config.tensor_parallel * config.pipeline_parallel
    gpu_limit = str(gpu_count) if gpu_count > 1 else "1"

    # Memory estimate
    mem_gb = max(svc.gpu_memory_mb // 1024 + 4, 16) if svc.gpu_memory_mb else 32

    llmisvc: dict[str, Any] = {
        "apiVersion": "serving.kserve.io/v1alpha1",
        "kind": "LLMInferenceService",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "aios.stack": stack_name,
                "aios.service": svc.name,
                "aios.runtime": svc.runtime,
            },
        },
        "spec": {
            "model": {
                "uri": f"hf://{svc.model}" if svc.model and not svc.model.startswith("hf://") else svc.model,
                "name": svc.model.split("/")[-1] if svc.model else svc.name,
            },
            "replicas": config.replicas,
            "template": {
                "containers": [{
                    "name": "main",
                    "image": image,
                    "args": args if args else None,
                    "resources": {
                        "limits": {
                            "nvidia.com/gpu": gpu_limit,
                            "cpu": str(max(gpu_count * 4, 8)),
                            "memory": f"{mem_gb}Gi",
                        },
                    },
                    "env": [{"name": k, "value": v} for k, v in svc.env.items()],
                }],
            },
        },
    }

    # Router config (Gateway API integration)
    llmisvc["spec"]["router"] = {
        "gateway": {},   # Managed by KServe
        "route": {},     # Managed HTTPRoute
    }

    # Scheduler config (llm-d v0.5 integration)
    # Features: KV-cache aware routing, prefix-cache locality,
    # utilization-based load balancing, fairness/prioritization
    llmisvc["spec"]["scheduler"] = {
        "prefixCacheAware": True,
        "utilizationBalancing": True,
    }

    # Tensor/Pipeline parallelism
    if config.tensor_parallel > 1 or config.pipeline_parallel > 1:
        llmisvc["spec"]["parallelism"] = {}
        if config.tensor_parallel > 1:
            llmisvc["spec"]["parallelism"]["tensorParallel"] = config.tensor_parallel
        if config.pipeline_parallel > 1:
            llmisvc["spec"]["parallelism"]["pipelineParallel"] = config.pipeline_parallel

    # P/D disaggregation (llm-d v0.5: hierarchical KV offloading)
    if config.enable_pd_disagg:
        llmisvc["spec"]["disaggregation"] = {
            "enabled": True,
            "prefillReplicas": max(1, config.replicas // 3),
            "decodeReplicas": config.replicas - max(1, config.replicas // 3),
            "kvOffloading": "hierarchical",  # llm-d v0.5
        }

    # Clean up None values
    containers = llmisvc["spec"]["template"]["containers"]
    for c in containers:
        if c.get("args") is None:
            del c["args"]
        if not c.get("env"):
            del c["env"]

    return llmisvc


def _to_deployment(svc: ServiceDef, stack_name: str, namespace: str) -> list[dict[str, Any]]:
    """Convert a non-LLM service to a standard K8s Deployment + Service."""
    name = f"aios-{stack_name}-{svc.name}"
    image = svc.image or RUNTIME_IMAGES.get(svc.runtime, "")

    resources: list[dict[str, Any]] = []

    deploy = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"aios.stack": stack_name, "aios.service": svc.name},
        },
        "spec": {
            "replicas": svc.replicas,
            "selector": {"matchLabels": {"aios.service": f"{stack_name}-{svc.name}"}},
            "template": {
                "metadata": {"labels": {"aios.service": f"{stack_name}-{svc.name}"}},
                "spec": {
                    "containers": [{
                        "name": svc.name,
                        "image": image,
                        "ports": [{"containerPort": svc.port}] if svc.port else [],
                        "env": [{"name": k, "value": v} for k, v in svc.env.items()],
                    }],
                },
            },
        },
    }

    if svc.gpu_required:
        deploy["spec"]["template"]["spec"]["containers"][0]["resources"] = {
            "limits": {"nvidia.com/gpu": "1"},
        }
        deploy["spec"]["template"]["spec"]["runtimeClassName"] = "nvidia"

    resources.append(deploy)

    if svc.port:
        resources.append({
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name, "namespace": namespace},
            "spec": {
                "selector": {"aios.service": f"{stack_name}-{svc.name}"},
                "ports": [{"port": svc.port, "targetPort": svc.port}],
            },
        })

    return resources


def generate_kserve_install_commands(k3s: bool = True) -> list[str]:
    """Generate commands to install KServe + LLMInferenceService on K3s."""
    cmds = [
        "# Install KServe v0.17 with LLMInferenceService support",
        "curl -fsSL https://github.com/kserve/kserve/releases/download/v0.17.0/llmisvc-dependency-install.sh | bash",
        "kubectl apply -f https://github.com/kserve/kserve/releases/download/v0.17.0/kserve-crds.yaml",
        "kubectl apply -f https://github.com/kserve/kserve/releases/download/v0.17.0/kserve.yaml",
        "kubectl apply -f https://github.com/kserve/kserve/releases/download/v0.17.0/kserve-cluster-resources.yaml",
    ]
    if k3s:
        cmds.insert(0, "# Ensure K3s is running with GPU Operator")
        cmds.insert(1, "# kubectl get nodes  # verify cluster")
    return cmds
