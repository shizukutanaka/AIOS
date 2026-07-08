# Runtime Module Rules

- vLLM metrics: `vllm:` prefix (colon)
- SGLang metrics: `sglang_` prefix (underscore)
- Ollama API: /api/tags, /api/generate, /api/ps
- Engine health check timeout: 5 seconds max
- Broker profile detection: nvidia → amd → intel → npu → cpu-only
- NEVER add external Python deps — stdlib only
- Model recommendations: 34 models in DB, sorted by VRAM fit
- Opt-in engines (LMDeploy, TensorRT-LLM/`trtllm-serve`, LM Studio): all
  OpenAI-compatible `/v1/*`, no Prometheus metrics contract
  (`scrape_metrics()` returns basic status, matching Ollama's honest
  fallback — never guess at metric names). `EngineHealth.engine` MUST
  exactly match the key in `get_adapter()`/`discover_engines()`'s adapters
  dict (`"lmdeploy"`, `"tensorrt_llm"`, `"lm_studio"` — underscore, not
  hyphen) since `BrokerRouter.route()` calls `get_adapter(h.engine, ...)`.
  `EngineEndpoints.lmdeploy`/`tensorrt_llm`/`lm_studio` default to `""` and
  are excluded from `to_dict()` unless configured — never probed by default.
