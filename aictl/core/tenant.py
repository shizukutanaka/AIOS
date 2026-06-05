"""Multi-tenant isolation: workload classes with resource limits.

Provides tenant-level isolation for enterprise deployments:
  - Tenant classes (regulated, standard, dev) with different resource limits
  - cgroup-based CPU/memory/GPU isolation
  - Network namespace separation (future: NetworkPolicy generation)
  - Audit logging per tenant
  - Rate limiting per tenant via API keys

Based on enterprise spec section 5 (tenant-class.regulated.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TenantClass:
    name: str                    # regulated | standard | dev
    max_gpu_slices: int = 1      # Max MIG slices or full GPUs
    max_memory_gb: int = 32      # Max RAM allocation
    max_vram_gb: int = 24        # Max VRAM allocation
    max_models: int = 3          # Max concurrent models
    max_requests_per_min: int = 60
    max_tokens_per_min: int = 100000
    allow_internet: bool = True  # Network egress
    require_signed_models: bool = False
    audit_level: str = "standard"  # minimal | standard | detailed
    namespace: str = ""          # K8s namespace (empty = default)
    priority: int = 1            # Scheduling priority (higher = more important)


# Pre-defined tenant classes
TENANT_CLASSES: dict[str, TenantClass] = {
    "regulated": TenantClass(
        name="regulated",
        max_gpu_slices=2,
        max_memory_gb=64,
        max_vram_gb=80,
        max_models=5,
        max_requests_per_min=1000,
        max_tokens_per_min=500000,
        allow_internet=False,
        require_signed_models=True,
        audit_level="detailed",
        namespace="regulated",
        priority=10,
    ),
    "standard": TenantClass(
        name="standard",
        max_gpu_slices=1,
        max_memory_gb=32,
        max_vram_gb=24,
        max_models=3,
        max_requests_per_min=120,
        max_tokens_per_min=200000,
        allow_internet=True,
        require_signed_models=False,
        audit_level="standard",
        namespace="default",
        priority=5,
    ),
    "dev": TenantClass(
        name="dev",
        max_gpu_slices=1,
        max_memory_gb=16,
        max_vram_gb=8,
        max_models=1,
        max_requests_per_min=30,
        max_tokens_per_min=50000,
        allow_internet=True,
        require_signed_models=False,
        audit_level="minimal",
        namespace="dev",
        priority=1,
    ),
}


@dataclass
class Tenant:
    id: str
    name: str
    tenant_class: str = "standard"
    api_key_ids: list[str] = field(default_factory=list)
    active_models: list[str] = field(default_factory=list)
    total_requests: int = 0
    total_tokens: int = 0


def get_tenant_class(name: str) -> TenantClass:
    """Get tenant class."""
    return TENANT_CLASSES.get(name, TENANT_CLASSES["standard"])


def generate_k8s_namespace(tenant: Tenant) -> dict[str, Any]:
    """Generate K8s Namespace with resource quotas for a tenant."""
    tc = get_tenant_class(tenant.tenant_class)

    ns = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": tc.namespace or f"aios-{tenant.id}",
            "labels": {
                "aios.tenant": tenant.id,
                "aios.tenant-class": tc.name,
            },
        },
    }

    quota = {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {
            "name": f"aios-quota-{tenant.id}",
            "namespace": tc.namespace or f"aios-{tenant.id}",
        },
        "spec": {
            "hard": {
                "requests.cpu": str(tc.max_gpu_slices * 8),
                "requests.memory": f"{tc.max_memory_gb}Gi",
                "limits.cpu": str(tc.max_gpu_slices * 16),
                "limits.memory": f"{tc.max_memory_gb * 2}Gi",
                "requests.nvidia.com/gpu": str(tc.max_gpu_slices),
            },
        },
    }

    resources = [ns, quota]

    # Network policy for regulated tenants
    if not tc.allow_internet:
        resources.append({
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": f"aios-deny-egress-{tenant.id}",
                "namespace": tc.namespace or f"aios-{tenant.id}",
            },
            "spec": {
                "podSelector": {},
                "policyTypes": ["Egress"],
                "egress": [
                    {
                        "to": [{"namespaceSelector": {"matchLabels": {"aios.tenant": tenant.id}}}],
                    },
                    {
                        "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}],
                        "ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}],
                    },
                ],
            },
        })

    return {"apiVersion": "v1", "kind": "List", "items": resources}


def generate_cgroup_limits(tenant: Tenant) -> dict[str, str]:
    """Generate cgroup v2 resource limits for local (non-K8s) mode."""
    tc = get_tenant_class(tenant.tenant_class)
    return {
        "MemoryMax": f"{tc.max_memory_gb}G",
        "CPUQuota": f"{tc.max_gpu_slices * 400}%",
        "IOWeight": str(min(tc.priority * 20, 100)),
        "TasksMax": str(tc.max_models * 100 + 200),
    }
