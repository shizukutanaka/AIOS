"""Auto-scaler: queue-depth and KV-cache based replica scaling.

Two modes:
  1. Local (Quadlet): Start/stop additional Podman containers
  2. K8s (KEDA): Generate ScaledObject manifests

Scaling signals (from research, April 2026):
  - vllm:num_requests_waiting > threshold → scale up
  - KV cache utilization > 0.9 → scale up
  - Queue empty for cooldown period → scale down
  - NEVER scale on CPU — it's meaningless for LLM inference

Based on:
  - KEDA v2.19 with Prometheus trigger
  - vLLM production-stack Helm chart autoscaling
  - KServe KEDA integration
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from aictl.runtime.adapters import get_adapter


@dataclass
class ScalePolicy:
    min_replicas: int = 1
    max_replicas: int = 8
    queue_depth_threshold: int = 5      # Scale up when queue > this
    kv_cache_threshold: float = 0.9     # Scale up when KV > this
    scale_up_cooldown_s: int = 60       # Min seconds between scale-ups
    scale_down_cooldown_s: int = 300    # Min seconds between scale-downs
    scale_up_step: int = 1              # Replicas to add per scale-up
    scale_down_step: int = 1            # Replicas to remove per scale-down


@dataclass
class ScaleDecision:
    action: str = "none"          # none | scale_up | scale_down
    current_replicas: int = 1
    desired_replicas: int = 1
    reason: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        """Set defaults for scaling decision."""
        if self.timestamp == 0:
            self.timestamp = time.time()


class AutoScaler:
    """Local auto-scaler that monitors engine metrics and adjusts replicas."""

    def __init__(self, engine: str, endpoint: str,
                 policy: ScalePolicy | None = None):
        """Initialize autoscaler."""
        self.engine = engine
        self.endpoint = endpoint
        self.policy = policy or ScalePolicy()
        self._current_replicas = 1
        self._last_scale_up = 0.0
        self._last_scale_down = 0.0
        self._history: list[ScaleDecision] = []

    def evaluate(self) -> ScaleDecision:
        """Evaluate current metrics and decide whether to scale."""
        adapter = get_adapter(self.engine, self.endpoint)
        if not adapter:
            return ScaleDecision(reason="No adapter available")

        try:
            health = adapter.health()
            if not health.reachable:
                return ScaleDecision(reason="Engine unreachable")

            metrics = adapter.scrape_metrics()
        except Exception as e:
            return ScaleDecision(reason=f"Metrics error: {e}")

        now = time.time()
        decision = ScaleDecision(
            current_replicas=self._current_replicas,
            desired_replicas=self._current_replicas,
            metrics={
                "queue_depth": float(metrics.queue_depth),
                "active_requests": float(metrics.active_requests),
                "kv_cache_util": metrics.kv_cache_utilization,
                "ttft_p95_ms": metrics.ttft_ms_p95,
            },
        )

        # Scale UP conditions
        should_scale_up = False
        reasons: list[str] = []

        if metrics.queue_depth > self.policy.queue_depth_threshold:
            should_scale_up = True
            reasons.append(f"queue={metrics.queue_depth}>{self.policy.queue_depth_threshold}")

        if metrics.kv_cache_utilization > self.policy.kv_cache_threshold:
            should_scale_up = True
            reasons.append(f"kv_cache={metrics.kv_cache_utilization:.2f}>{self.policy.kv_cache_threshold}")

        if should_scale_up:
            if self._current_replicas >= self.policy.max_replicas:
                decision.reason = f"At max replicas ({self.policy.max_replicas})"
            elif now - self._last_scale_up < self.policy.scale_up_cooldown_s:
                decision.reason = "Scale-up cooldown active"
            else:
                new = min(self._current_replicas + self.policy.scale_up_step,
                          self.policy.max_replicas)
                decision.action = "scale_up"
                decision.desired_replicas = new
                decision.reason = "; ".join(reasons)
                self._last_scale_up = now
                self._current_replicas = new
        else:
            # Scale DOWN: queue empty and low utilization
            if (metrics.queue_depth == 0 and
                    metrics.active_requests == 0 and
                    self._current_replicas > self.policy.min_replicas):
                if now - self._last_scale_down < self.policy.scale_down_cooldown_s:
                    decision.reason = "Scale-down cooldown active"
                else:
                    new = max(self._current_replicas - self.policy.scale_down_step,
                              self.policy.min_replicas)
                    decision.action = "scale_down"
                    decision.desired_replicas = new
                    decision.reason = "Queue empty, low utilization"
                    self._last_scale_down = now
                    self._current_replicas = new

        self._history.append(decision)
        if len(self._history) > 100:
            self._history = self._history[-100:]

        return decision


def generate_keda_scaled_object(
    deployment_name: str,
    namespace: str = "default",
    prometheus_url: str = "http://prometheus:9090",
    policy: ScalePolicy | None = None,
    engine: str = "vllm",
) -> dict[str, Any]:
    """Generate a KEDA ScaledObject for K8s autoscaling.

    Uses Prometheus trigger with vLLM/SGLang queue depth metric.
    """
    if policy is None:
        policy = ScalePolicy()

    # Select metric based on engine
    if engine == "vllm":
        metric_name = "vllm:num_requests_waiting"
        query = "avg(vllm:num_requests_waiting)"
    elif engine == "sglang":
        metric_name = "sglang_num_requests_waiting"
        query = "avg(sglang_num_requests_waiting)"
    else:
        metric_name = "num_requests_waiting"
        query = "avg(num_requests_waiting)"

    return {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "ScaledObject",
        "metadata": {
            "name": f"{deployment_name}-autoscaler",
            "namespace": namespace,
            "labels": {"aios.autoscaler": "true"},
        },
        "spec": {
            "scaleTargetRef": {"name": deployment_name},
            "minReplicaCount": policy.min_replicas,
            "maxReplicaCount": policy.max_replicas,
            "pollingInterval": 15,
            "cooldownPeriod": policy.scale_down_cooldown_s,
            "triggers": [
                {
                    "type": "prometheus",
                    "metadata": {
                        "serverAddress": prometheus_url,
                        "metricName": metric_name,
                        "query": query,
                        "threshold": str(policy.queue_depth_threshold),
                    },
                },
            ],
        },
    }


def generate_hpa_manifest(
    deployment_name: str,
    namespace: str = "default",
    policy: ScalePolicy | None = None,
) -> dict[str, Any]:
    """Generate a standard K8s HPA with custom metrics (for non-KEDA clusters)."""
    if policy is None:
        policy = ScalePolicy()

    return {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": {
            "name": f"{deployment_name}-hpa",
            "namespace": namespace,
        },
        "spec": {
            "scaleTargetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": deployment_name,
            },
            "minReplicas": policy.min_replicas,
            "maxReplicas": policy.max_replicas,
            "metrics": [
                {
                    "type": "Pods",
                    "pods": {
                        "metric": {"name": "vllm_queue_depth"},
                        "target": {
                            "type": "AverageValue",
                            "averageValue": str(policy.queue_depth_threshold),
                        },
                    },
                },
            ],
            "behavior": {
                "scaleUp": {
                    "stabilizationWindowSeconds": 0,
                    "policies": [{"type": "Pods", "value": policy.scale_up_step,
                                  "periodSeconds": 60}],
                },
                "scaleDown": {
                    "stabilizationWindowSeconds": policy.scale_down_cooldown_s,
                    "policies": [{"type": "Pods", "value": policy.scale_down_step,
                                  "periodSeconds": 60}],
                },
            },
        },
    }
