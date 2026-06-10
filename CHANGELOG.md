# Changelog

## Unreleased — Quality & correctness fixes

### Fixed
- **Quality gate** (`aictl/cmd/gate.py`): when `ruff`/`mypy` are invoked via
  `python3 -m <tool>` but not installed, the interpreter exits non-zero with
  `No module named <tool>` on stderr and empty stdout. The MyPy step misread this
  as a phantom `999999`-error regression (failing the gate) and the Ruff step hit
  an `IndexError`. Both now detect the not-installed case and skip cleanly.
- **Semantic cache** (`aictl/core/sem_cache.py`): `stats()` computed the DB lifetime
  tokens-saved total then discarded it, reporting only the in-process session value.
  Now exposed as `lifetime_tokens_saved`. Eviction `LIMIT` is parameter-bound.
- **Router** (`aictl/runtime/router.py`): removed dead `fallback_used` branch in the
  direct-selection path (the comparison was always false).
- **RAG chunking** (`aictl/core/rag.py`): `chunk_text` no longer raises `ValueError`
  or silently drops oversized paragraphs when `overlap >= chunk_size`.
- **Route explain** (`aictl/cmd/route.py`): keyword labels strip the regex `\b`
  token explicitly instead of `str.strip('\\b')` (which could mangle keywords).
- Two tests in `tests/test_quadlet_daemon.py` used a hardcoded absolute path; now
  repo-relative.

### Changed
- Project source is now tracked in the repository (was shipped only as a zip).
- Documentation counts reconciled with reality: 1776 tests, 66 CLI commands,
  19 MCP tools, 150 modules.
- Added `tests/test_improvements_v16.py` (7 regression tests).

## v1.6.0 (2026-04-25) — Competitor Gap Release

### New Commands

- `aictl diff <model-a> <model-b>` — A/B output comparison. Run both models on the same prompts, compute Jaccard word overlap, latency delta, cost delta. Finds the better model for your use case before you commit to switching. Zero competitor has this as a CLI.
- `aictl prompt save/list/get/history/run/export/delete` — First-class prompt management with versioning. Prompts are code; treat them as such. Integrates with `aictl eval` via `--format eval` export.
- `aictl route show/ask/config/test/batch` — Complexity-aware request routing. Scores 0–100, classifies SIMPLE/MEDIUM/COMPLEX, dispatches to the cheapest model that can handle it. Replaces LiteLLM's $19/month routing feature with a local, zero-dep equivalent. 75%+ accuracy on built-in benchmark.
- `aictl spec recommend/bench/auto/vllm/sglang/drafts` — Speculative decoding advisor. 2-3x throughput at zero quality cost. Shows best draft model, acceptance rate, and ready-to-paste vLLM/SGLang commands.
- `aictl fit <model>` — VRAM fit checker: answers "will this model run on my GPU?" before downloading anything. Shows per-quantization breakdown (FP16/FP8/Q8/AWQ/Q4/Q3) with headroom and recommended alternative models.
- `aictl quant recommend/compare <model>` — Quantization advisor: picks the best quantization format for your GPU, use-case, and engine. Based on April 2026 empirical quality benchmarks.
- `aictl troubleshoot` — Symptom-based diagnosis: diagnoses OOM, slow inference, wrong output, and startup failures with one recommended fix per symptom.
- `aictl rag index/ask/search/status/reset` — Zero-config local RAG (GPT4All LocalDocs equivalent). Drop a folder of Markdown/PDF/code files, query instantly. SQLite + embedding backend, zero external deps.
- `aictl guard scan/test` — Local PII detection and content filtering. Detects 9 PII types (email, phone, credit card, JP postal, SSN, IPv4, API keys, My Number) and 4 content policies (prompt injection, jailbreak, system leak, token bomb). Fully local — no data leaves the machine.
- `aictl cache status/clear` — Manage the semantic response cache. Shows hit rate, tokens saved, and DB size.
- `aictl perf` — Per-command performance summary (P50/P95/P99 latency, failure count). Auto-collected from every invocation.
- `aictl dash` — All-in-one dashboard: system, engines, cache, cost, perf, guardrails, RAG in one screen. Supports `--watch` for live refresh.
- `aictl help <topic>` — Discovery-oriented help with 7 topics: getting-started, everyday, models, cost, compliance, kubernetes, advanced.
- `aictl tco` — True Cost of Ownership: electricity (¥/kWh) + GPU depreciation + cloud fallback. No competitor shows this.
- `aictl quota create/list/report/reset` — Per-team token quotas with chargeback. SDK auto-tracks usage when `AICTL_TEAM` env var is set.
- `aictl batch add/list/run/remove/status` — Background batch jobs that run during GPU idle time (embed, classify, summarize).
- `aictl update check/models/self` — Self-update via git pull or pip, model catalog refresh from upstream.
- `aictl setup` — Apple-style guided 5-step onboarding: hardware detect → model pick → engine check → model pull → first inference.

### SDK Enhancements
- `aictl.ai.classify(text, categories)` — Classify text into provided categories.
- `aictl.ai.structured(prompt, schema)` — Get structured JSON output matching a schema.
- `aictl.ai.configure(cost_budget_usd, prefer, engine, model)` — Runtime configuration.
- `aictl.ai.ask(..., context=text)` — Optional background context parameter.
- `response.cost` — Every API response now carries per-call cost (USD + JPY).
- `response.cached` — True when the semantic cache served the response (cost $0).

### Infrastructure
- **Semantic cache** (`aictl/core/sem_cache.py`) — SQLite-backed semantic response cache. Returns cached responses for similar (not identical) prompts using cosine similarity. ~40% cost reduction on repetitive workloads.
- **PII + guardrails** (`aictl/core/guard.py`) — Local guardrail engine with Luhn validation for credit cards and My Number, and 4 content filter patterns.
- **Per-call cost** (`aictl/core/cost_per_call.py`) — Pricing table for 15 cloud models + electricity-based local cost. Cost visible on every response.
- **Prefix-cache routing** (`aictl/runtime/prefix_route.py`) — KV cache locality routing (SGLang RadixAttention-style). Tracks prompt prefix hashes per endpoint and biases routing toward cache-warm endpoints.
- **Self-healing** (`aictl/core/self_heal.py`) — Auto-recovery for port conflicts, missing directories, OOM (context shrink), and transient network errors.
- **Performance instrumentation** (`aictl/core/perf.py`) — Zero-boilerplate per-command timing with P50/P95/P99 aggregation.
- **First-run welcome** (`aictl/core/welcome.py`) — Friendly onboarding screen with hardware-aware next-step suggestion.
- **Human error messages** (`aictl/core/errors.py`) — 9 exception types with plain-language messages and exactly one suggested action.
- Router (`aictl/runtime/router.py`) integrated with prefix_route for real prompt-level KV cache locality scoring.

### Quality
- Tests: 679 → **824** (+145 new tests)
- Chaos tests: disk failures, network unavailability, corrupted state files, concurrent writes, Unicode errors — all handled gracefully.
- Input validation: empty model names, unknown GPUs, unknown models — all return non-zero with helpful messages.
- `_AmbientContext.reset_for_testing()` — SDK singleton reset for test isolation.



### CLI
- 46 Python + 29 Go commands
- `deploy optimize` — vLLM flag auto-tuning (FP8, KV cache, TP)
- `deploy modelservice` — llm-d ModelService Helm values (3 presets)
- `deploy disagg` — P/D disaggregated deployment (NixlConnector/LMCacheConnector)
- Shell completion: bash, zsh, fish

### Runtime
- 29-model DB (DeepSeek V4 1T MoE, GLM-5, Gemma 4)
- 5 NPU vendors (NVIDIA, Intel, AMD, Huawei Ascend, Qualcomm)
- 7 GPU cost comparison (April 2026 pricing, B200: $0.89/Mtoken)
- Cloud fallback (5 providers: OpenAI, OpenRouter, Together, Groq, Fireworks)
- Speculative decoding (EAGLE3, MTP, N-gram)
- vLLM optimization flag generator (FP8 KV cache, chunked prefill, prefix caching)

### Structured Output
- 5 formats: guided_json, response_format, structured_outputs, Ollama format, Ollama schema
- Tool calling (OpenAI-compatible function calling)
- Mock engine generates schema-conforming JSON

### K8s Export (7 formats)
- KServe LLMInferenceService
- Gateway API InferencePool/InferenceModel v1
- KEDA ScaledObject
- HPA (autoscaling/v2)
- Dynamo DGDR (InferenceDeployment)
- llm-d P/D Disaggregation
- llm-d ModelService Helm values

### Monitoring
- OTel GenAI Semantic Conventions (gen_ai.* + aios.*)
- Grafana dashboard (8 panels)
- Prometheus alerting rules (9 rules, 3 groups)

### MCP Server (10 tools)
- JSON-RPC 2.0 over stdio
- Tools: health, status, recommend, cost, optimize, security, recipes, meter, lora, fabric

### Infrastructure
- Docker Compose quick start
- bootc Containerfile (Fedora 42)
- systemd service unit
- Makefile (20 targets)
- GitHub Actions CI (Python 3.11-3.13 + Go 1.23)

### Quality
- 676 tests (659 Python + 17 Go)
- 0 compile errors, 0 import errors, 0 bare excepts
- 81% return type hint coverage
- 99.7% docstring coverage
- PEP 561 py.typed marker

## v1.0.0 (2026-04-10) — Initial Release

- 46 CLI commands, 22 REST API endpoints
- Mock engine with OpenAI-compatible API
- 10 deployment recipes
- Hardware auto-detection (GPU/NPU/CPU)
- Cosign v3 model verification
- cgroup v2 process isolation
- Token metering and quota enforcement
