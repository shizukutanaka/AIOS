"""K3s autopromote: migrate from local Quadlet mode to K3s cluster.

Based on research (April 2026):
  - K3s v1.35.3+k3s1 (latest stable)
  - SQLite -> embedded etcd: add --cluster-init flag
  - HA requires odd number of server nodes (3 min)
  - GPU Operator v26.3.0 for NVIDIA support
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from aictl.core.state import StateStore
from aictl.runtime.nodes import NodeManager


@dataclass
class PromoteStep:
    order: int
    action: str
    description: str
    command: str = ""
    status: str = "pending"


@dataclass
class PromotePlan:
    ready: bool = False
    reason: str = ""
    steps: list[PromoteStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_prerequisites() -> tuple[bool, list[str]]:
    """Check prerequisites."""
    issues: list[str] = []
    if shutil.which("k3s"):
        issues.append("K3s already installed")
    if not shutil.which("curl"):
        issues.append("curl required for K3s install")
    if not shutil.which("systemctl"):
        issues.append("systemctl required")
    try:
        st = os.statvfs("/var")
        if (st.f_bavail * st.f_frsize) / (1024**3) < 2:
            issues.append("Need 2GB+ free on /var")
    except OSError:
        pass  # best-effort; failure is non-critical
    return len(issues) == 0, issues


def generate_promote_plan(store: StateStore) -> PromotePlan:
    """Generate promote plan."""
    plan = PromotePlan()
    mgr = NodeManager(store)
    cs = mgr.load_cluster()
    node = store.load_node()
    stacks = store.load_stacks()

    active_peers = [p for p in cs.peers if p.status == "active"]
    if not active_peers:
        plan.ready = False
        plan.reason = "No active peers"
        return plan

    plan.ready = True
    plan.reason = f"{len(active_peers) + 1} nodes ready"

    plan.steps = [
        PromoteStep(1, "snapshot", "Create state snapshot", "aictl snapshot create --label pre-k3s"),
        PromoteStep(2, "install_k3s", "Install K3s with --cluster-init",
                    "curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='server --cluster-init' sh -"),
        PromoteStep(3, "get_token", "Get join token", "cat /var/lib/rancher/k3s/server/node-token"),
    ]

    for i, peer in enumerate(active_peers):
        plan.steps.append(PromoteStep(
            4 + i, f"join_{peer.node_id[:8]}",
            f"Join {peer.hostname} ({peer.address})",
            f"K3S_URL=https://{node.hostname}:6443 K3S_TOKEN=<token> k3s agent",
        ))

    n = 4 + len(active_peers)
    if node.gpu_count > 0:
        plan.steps.append(PromoteStep(n, "gpu_operator",
            "Install NVIDIA GPU Operator v26.3.0",
            "helm install gpu-operator nvidia/gpu-operator --version=v26.3.0 -n gpu-operator --create-namespace"))
        n += 1

    if stacks:
        plan.steps.append(PromoteStep(n, "migrate_stacks",
            f"Convert {len(stacks)} stacks to K8s", "aictl apply -f <stack> --k8s"))

    plan.warnings.append("etcd 3.5->3.6 migration: ensure v3.5.26 intermediate step")
    return plan


def stack_to_k8s(stack_name: str, store: StateStore) -> dict[str, Any]:
    """Convert a Stack to K8s Deployment + Service manifests."""
    stacks = store.load_stacks()
    stack = next((s for s in stacks if s.name == stack_name), None)
    if not stack:
        return {}

    items: list[dict[str, Any]] = []
    for svc in stack.services:
        name = svc.get("name", "")
        if not name:
            continue
        deploy = {
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": f"aios-{stack_name}-{name}",
                         "labels": {"aios.stack": stack_name}},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"aios.service": name}},
                "template": {
                    "metadata": {"labels": {"aios.service": name}},
                    "spec": {"containers": [{
                        "name": name,
                        "image": svc.get("image", ""),
                        "ports": [{"containerPort": svc.get("port", 8080)}] if svc.get("port") else [],
                        **({"resources": {"limits": {"nvidia.com/gpu": "1"}}} if svc.get("gpu_required") else {}),
                    }]},
                },
            },
        }
        items.append(deploy)
        if svc.get("port"):
            items.append({
                "apiVersion": "v1", "kind": "Service",
                "metadata": {"name": f"aios-{stack_name}-{name}"},
                "spec": {"selector": {"aios.service": name},
                         "ports": [{"port": svc["port"], "targetPort": svc["port"]}]},
            })
    return {"apiVersion": "v1", "kind": "List", "items": items}
