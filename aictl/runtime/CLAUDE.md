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
- KV-budget hard filter: `BrokerRouter.route()` rejects any engine whose
  `kv_cache_utilization` exceeds `slo_target.kv_cache_max` (default 0.9),
  applied AFTER metrics collection (not in `_hard_filter`, which runs
  before metrics are scraped). `_fallback`'s priority-order path is the
  safety net if every candidate ends up rejected this way.
