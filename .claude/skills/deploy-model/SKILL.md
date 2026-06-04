---
name: deploy-model
description: Deploy an AI model with zero-config (DGDR-style)
argument-hint: [model-name]
---
# Deploy Model

## Workflow
1. `aictl deploy plan <model> --hardware auto` → resource estimation
2. Review: GPUs needed, VRAM, meets SLA?
3. `aictl deploy manifest <model>` → generate InferenceDeployment YAML
4. For local: `aictl recipe run local-chat` (Ollama) or `local-gpu-chat` (vLLM)
5. For K8s: `kubectl apply -f manifest.yaml`

## Key decisions
- Model < 8B params → Ollama (GGUF quantized) → single RTX 4090
- Model 8-70B → vLLM (FP16 or FP8) → 1-2x H100
- Model > 70B → vLLM + P/D disaggregation → 4-8x H100
- Need < 200ms TTFT → P/D disagg recommended
- Cost constraint → `aictl cost compare` first

## Checks after deploy
- `aictl net` → engine reachable
- `aictl trace` → full request path timing
- `aictl watch` → continuous monitoring
