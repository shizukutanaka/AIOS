# ADR-005: Kubernetes Gateway API Inference Extension

## Status: Accepted (2026-04-10)

## Context
The Gateway API Inference Extension became v1 (stable) in 2026.
It introduces InferencePool and InferenceModel as first-class K8s resources.

Supported by: Istio v1.28, NGINX GW Fabric v2.5, kgateway v2.0, GKE.

Key features:
- InferencePool: group of model server pods with Endpoint Picker (EPP)
- InferenceModel: model routing with criticality (Critical/Standard/Sheddable)
- EPP: KV-cache utilization, queue depth, LoRA-aware routing
- Body-based routing (BBR) for model selection from request body

## Decision
aictl supports three K8s export formats:
1. `cluster export` → KServe LLMInferenceService (existing)
2. `cluster gateway` → Gateway API InferencePool/InferenceModel (new)
3. `scale keda` → KEDA ScaledObject (existing)

Users choose based on their cluster's gateway implementation.

## Consequences
- InferencePool v1 is the forward-looking standard
- KServe remains supported for existing deployments
- EPP provides inference-aware routing (vs. round-robin)
- LoRA adapter routing maps to InferenceModel targetModels
