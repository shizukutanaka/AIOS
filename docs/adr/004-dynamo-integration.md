# ADR-004: NVIDIA Dynamo Integration

## Status: Accepted (2026-04-10)

## Context
NVIDIA Dynamo (v0.8+, production March 2026) provides:
- KVBM: 4-tier KV cache management (HBM → DRAM → SSD → Remote)
- NIXL: Unified data transfer across memory tiers
- ModelExpress: 7x faster model loading via GPU-to-GPU streaming
- Planner: SLA-driven autoscaling
- Grove: Topology-aware gang scheduling for NVL72
- AIConfigurator: Simulates 10K+ configs to find optimal deployment

Dynamo is the NVIDIA "inference OS" and has adoption from
BlackRock, ByteDance, Pinterest, SoftBank, and 20+ cloud providers.

## Decision
aictl integrates with Dynamo as an **optional accelerator**:
1. Detect Dynamo components (binary, NIXL lib, Grove CRD)
2. Generate KVBM config from detected memory fabric
3. Provide DGDR-style zero-config deployment (model + SLA → manifest)
4. AIConfigurator-style resource estimation (params → VRAM → GPUs → TPS)
5. KServe manifests include llm-d v0.5 scheduler config

aictl does NOT require Dynamo — it works standalone with
vLLM/SGLang/Ollama on any Linux. Dynamo is additive.

## Consequences
- Users with NVIDIA GPUs get optimal KV cache management
- DGDR manifests can be applied to Dynamo-enabled clusters
- Non-NVIDIA users (AMD ROCm, Intel) are unaffected
- The fabric module aligns with KVBM's 4-tier model
