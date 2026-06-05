---
name: k8s-promote
description: Promote local deployment to K8s cluster
---
# K8s Promotion

## Steps
1. Install K3s: `aictl cluster promote`
2. Export manifests: `aictl cluster export <stack> > manifests.yaml`
3. Apply: `kubectl apply -f manifests.yaml`
4. KEDA autoscaling: `aictl scale keda <deployment> --threshold 5`
5. Tenant isolation: `aictl tenant namespace <id> --class regulated`

## Generated resources
- LLMInferenceService (KServe v0.17)
- llm-d v0.5 scheduler (prefix-cache aware, utilization balancing)
- KEDA ScaledObject (Prometheus trigger on vllm:num_requests_waiting)
- ResourceQuota + NetworkPolicy (regulated tenants)
- Service + Ingress

## Monitoring
- `aictl otel config > /etc/otel/config.yaml` → OTel Collector
- Prometheus: `curl :7700/metrics`
- Grafana: import dashboards from docs/grafana/
