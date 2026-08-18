# Changelog

## v1.7.0 — 2026-07-16 — Retrieval quality, layered routing, fairness & guardrail hardening

Highlights since v1.6.0 (features all additive and off-by-default/opt-in, zero new
external dependencies, still stdlib-only):

> **One behaviour change, only if you set `AIOS_STATE_DIR`, `AICTL_STATE_DIR` or
> `--state-dir`.** Those used to move only part of your state — `state.json`, the
> model registry, your API keys, the audit log and the metering ledger stayed in
> `~/.aios` regardless. They now follow the setting like everything else, so the
> files left behind need to come across once or they will read as empty. `aictl`
> detects this and prints the command; see **Upgrade notes** in `RELEASE.md`.
> Default `~/.aios` users are unaffected.

- **Retrieval:** hybrid dense+BM25 RAG retrieval with Reciprocal Rank Fusion; pluggable
  cross-encoder **reranker** (`rag search --rerank`, TEI-compatible `/rerank`, off by
  default); embedding-provider **capability detection** so the hash fallback is truly
  last-resort, with degraded-mode honesty flags in `rag status`/`cache status`.
- **Routing:** the embedding-**kNN** confidence-gated tie-breaker (`route --knn`) completes
  the rules → embedding → cascade layered-routing stack.
- **Guardrails:** the content-policy + PII-redaction gate now runs on real proxy traffic
  (opt-in, no-op by default); an optional Llama-Guard-style **model check** with an LRU
  verdict cache (DoS hardening, arXiv:2606.14517).
- **MCP:** 2026-07-28 spec compatibility (version negotiation, `server/discover`,
  `ttlMs`/`cacheScope`) plus **progress notifications** for long-running tool calls.
- **Fairness/cost:** `tco fairshare` advisory (Jain's fairness index over per-tenant token
  usage); carbon/energy advisor (`tco carbon`).
- **Catalog/advisors:** GLM-5.2 & Kimi K2.6 models, Medusa speculative-decoding method,
  vLLM v0.19 KV-offload hints, NVFP4 quant sweet-spot notes, Apple-Silicon unified-memory
  fit math, 3 new engine adapters (LMDeploy, TensorRT-LLM, LM Studio).

3433+ tests (all green), 80 Python + 29 Go commands, 19 MCP tools.

### Added
- **KV-budget hard filter in the router** (`aictl/runtime/router.py`): `SLOConfig.kv_cache_max`
  was already used by the governor and `optimize.py`'s recommendations, but `BrokerRouter` —
  the component that actually picks the next request's engine — never referenced it, only a
  soft "headroom" factor that could still let a near-exhausted engine win. `route()` now
  hard-rejects any engine over `kv_cache_max` with a `kv_cache_exhausted` reason code, the
  same way unreachable/wrong-status engines already are. If every candidate is rejected this
  way, the existing `_fallback` priority-order path still returns a reachable engine
  (degraded, not an outage).
- **LMDeploy, TensorRT-LLM, LM Studio engine adapters** (`aictl/runtime/adapters.py`):
  `runtime/adapters.py` only detected vLLM/SGLang/Ollama; the 2026 field also treats
  LMDeploy (TurboMind), TensorRT-LLM (`trtllm-serve`), and LM Studio as mainstream, all
  OpenAI-compatible. Opt-in only — `EngineEndpoints.lmdeploy`/`tensorrt_llm`/`lm_studio`
  default to `""` and are excluded from `to_dict()` until configured
  (`aictl config set engines.lmdeploy <url>`), so zero-config discovery/status/demo/gate
  is completely unaffected. `recommend`/`optimize`/`route` all consume `discover_engines()`
  generically, so all three widen automatically.

### Security
- **Guard content-policy + PII redaction gate in the proxy** (`aictl/daemon/proxy.py`):
  `core/guard.py` (9 PII types, 4 content policies, Unicode/homoglyph-hardened) was a
  manual-only tool — `aictl guard scan` / the MCP tool — never consulted on real
  inference traffic. A prompt-injection/jailbreak attempt sailed straight through to the
  engine, and PII an upstream model leaked in its response reached the client untouched.
  Two new `Config` fields, both default to a no-op (`guard_policy: off|warn|enforce`,
  `guard_redact_output: bool = False`), gate two new proxy checks: `_check_guard`
  (request-side content policy, before routing, both completions and embeddings) and
  `_redact_response_pii` (response-side PII redaction, non-streaming only — SSE has no
  buffering point today, documented not silently dropped). Redaction feeds the same
  `aios_guard_redactions_total` counter added below. Config re-read per request, so
  `aictl config set guard_policy enforce` takes effect without a proxy restart.

### Observability
- **Guard redactions metric** (`aictl/core/guard.py`, `aictl/metrics/prometheus.py`):
  `/metrics` now emits `aios_guard_redactions_total` — the last of
  `docs/IMPROVEMENTS.md` item J's value-prop counters that wasn't wired yet
  (cache/metering/cascade counters already were). `scan()` gained an opt-in
  `state_dir` kwarg to persist the lifetime tally; left at its default it
  stays the exact pure function it always was, so existing callers/tests are
  unaffected. `aictl guard scan --redact` always feeds the counter (resolves
  a concrete state dir regardless of `--state-dir`); the MCP guard tool does
  not yet (no state-dir plumbing there today — noted, not silently assumed).
  Route-cost-saved (the other item-J leftover) needs a baseline-cost
  methodology decision and remains future work.

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
- Documentation counts reconciled with reality: 1840 tests, 66 CLI commands,
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
