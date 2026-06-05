# ADR-005: Competitive Landscape and Differentiation

## Status: Accepted (2026-04-11)

## Context
Multiple open-source tools exist in the AI inference infrastructure space.
We must understand where aictl fits and what makes it unique.

## Competitive Analysis

### GPUStack (gpustack.ai)
**What:** Open-source GPU cluster manager with web UI.
**Overlap:** Multi-engine support (vLLM/SGLang/TRT-LLM), model deployment, metering.
**Differentiation from aictl:**
- GPUStack has a web UI dashboard; aictl is CLI-first
- GPUStack requires its own server process; aictl works standalone
- GPUStack doesn't provide OS-level features (bootc, cgroup isolation, DAMON)
- GPUStack doesn't generate K8s manifests (KServe, Gateway API)
- aictl has zero external deps; GPUStack requires Python packages
**Conclusion:** GPUStack is a cluster manager; aictl is an OS control plane.

### LiteLLM (github.com/BerriAI/litellm)
**What:** Multi-provider proxy (100+ providers), cost tracking, budget limits.
**Overlap:** OpenAI-compatible proxy, API key management.
**Differentiation from aictl:**
- LiteLLM is cloud-provider focused; aictl is local-first
- LiteLLM doesn't manage engines (vLLM/Ollama lifecycle)
- LiteLLM doesn't provide hardware detection or model recommendations
- aictl includes LiteLLM-like fallback via runtime/fallback.py
**Conclusion:** LiteLLM is a routing proxy; aictl is infrastructure management.

### Olla (github.com/thushan/olla)
**What:** Local LLM routing + failover between multiple instances.
**Overlap:** Multi-engine routing, health checking.
**Differentiation from aictl:**
- Olla is a lightweight router only; aictl manages the full stack
- Olla doesn't deploy engines; aictl generates Quadlet/systemd units
- Olla doesn't do security scanning, cost estimation, or K8s export
**Conclusion:** Olla is a router; aictl is the OS.

### LocalAI (github.com/mudler/LocalAI)
**What:** Drop-in OpenAI replacement for local inference.
**Overlap:** OpenAI-compatible API, local model serving.
**Differentiation from aictl:**
- LocalAI is a single inference server; aictl orchestrates multiple engines
- LocalAI doesn't provide multi-node clustering or K8s integration
- aictl delegates inference to vLLM/SGLang/Ollama; LocalAI runs its own
**Conclusion:** LocalAI is an engine; aictl orchestrates engines.

### NVIDIA Dynamo (github.com/ai-dynamo/dynamo)
**What:** Distributed inference framework with KVBM, NIXL, Grove.
**Overlap:** KV cache management, disaggregated serving.
**Differentiation from aictl:**
- Dynamo is NVIDIA-specific; aictl supports AMD/Intel/Apple
- Dynamo requires Kubernetes; aictl works standalone
- aictl integrates with Dynamo as an optional accelerator
**Conclusion:** Dynamo is an inference runtime; aictl is the OS layer above it.

## aictl's Unique Position

aictl is the **only** tool that combines ALL of:
1. ✅ OS-level control (bootc, cgroup, DAMON, PSI)
2. ✅ Multi-engine orchestration (vLLM + SGLang + Ollama)
3. ✅ Zero external Python dependencies
4. ✅ Local → K8s progression (single command)
5. ✅ 5 K8s export formats (KServe, Gateway API, KEDA, HPA, Dynamo)
6. ✅ Security scanning with remediation
7. ✅ GPU cost estimation with real pricing
8. ✅ Token metering with quota enforcement
9. ✅ Model supply chain (Cosign signatures + ORAS)
10. ✅ Full-stack mock demo (no GPU required)

## Decision
- Position aictl as "the OS control plane for AI inference"
- Integrate with (not replace) GPUStack, LiteLLM, Dynamo
- Cloud fallback via runtime/fallback.py for resilience
- Maintain zero-dep policy as core differentiator
