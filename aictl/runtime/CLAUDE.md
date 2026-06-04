# Runtime Module Rules

- vLLM metrics: `vllm:` prefix (colon)
- SGLang metrics: `sglang_` prefix (underscore)
- Ollama API: /api/tags, /api/generate, /api/ps
- Engine health check timeout: 5 seconds max
- Broker profile detection: nvidia → amd → intel → npu → cpu-only
- NEVER add external Python deps — stdlib only
- Model recommendations: 26 models in DB, sorted by VRAM fit
