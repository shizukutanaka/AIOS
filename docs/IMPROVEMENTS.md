# aictl — Improvement Dossier (competitive + arXiv gap analysis)

> Goal: 同種ソフト・arXiv を参考に改善点を洗い出す.
> Method: surveyed peer software (inference engines, LLM gateways, local-LLM tools)
> and 2025–2026 arXiv work, then mapped each gap to the concrete `aictl` module that
> would change. Every item notes **current state (with file ref)**, **what peers/SOTA do**,
> and a **zero-dep-friendly proposal** (the project's hard constraint: stdlib only).
> Date: 2026-06-05. Nothing here is implemented yet — this is the backlog.

## How aictl already compares

`aictl` is unusually broad for a local-first tool: it already ships engine auto-detect +
routing (`runtime/adapters.py`, `runtime/router.py`), VRAM-fit + quant advisors
(`cmd/fit.py`, `cmd/quant.py`), a speculative-decoding advisor (`cmd/spec.py`),
RadixAttention-style prefix-cache routing (`runtime/prefix_route.py`), a local semantic
cache (`core/sem_cache.py`), zero-config RAG (`core/rag.py`), PII/guardrails
(`core/guard.py`), 7 K8s export formats, Dynamo/MIG/LoRA, and a 19-tool MCP server.
The gaps below are where it trails current peers or recent research — not greenfield.

---

## A. RAG quality — the embedding is the weak link  ⭐ highest leverage

- **Current:** `core/rag.py:305` `embed_text()` calls `aictl.ai.embed()`, but when no engine
  is reachable it falls back to `_fallback_embedding` (`rag.py:321`) — a **64-dim SHA-256
  byte-distribution hash that is explicitly "NOT semantic"**. The semantic cache
  (`core/sem_cache.py`) shares this path, so cache *and* retrieval silently degrade to
  near-random similarity offline. Retrieval is also pure cosine over chunks — no lexical
  signal, no reranking.
- **Peers / SOTA:** GPT4All LocalDocs and LocalAI ship real on-device embedding models;
  the inference-serving survey *Taming the Titans* (arXiv:2504.19720) and the scheduling
  survey both highlight **RAG-aware caching** (CacheBlend) as a first-class technique.
- **Proposal (stdlib-friendly):**
  1. **Hybrid retrieval:** add a stdlib **BM25/TF-IDF** lexical scorer and fuse with the
     existing cosine score (reciprocal-rank fusion). Pure Python, zero deps, and it makes
     retrieval usable even with the hash fallback.
  2. **Pluggable real embeddings:** formalize an embedding-provider hook (Ollama
     `/api/embeddings`, vLLM/SGLang embedding endpoints) with capability detection, so the
     hash path is truly last-resort and is *flagged* in `rag status`/`cache status`.
     **Honesty half ✅ done (Pass 185):** two real defects fixed — (a) `sdk._embed`'s
     per-text fallback emitted 32-dim vectors (one raw sha256) while rag's fallback and
     its semantic-detector use FALLBACK_DIM (64), so an SDK-level fallback (engine up,
     embedding model not pulled — the most common degradation) produced vectors that
     `rag status` misreported as SEMANTIC, and a partially-failing batch mixed dims
     (cosine across mismatched dims silently returns 0.0). Fixed with batch-level
     fallback reusing `rag._fallback_embedding`. (b) `cache status` had no degraded
     flag at all — `SemanticCache.stats()` now returns `semantic_embeddings` (same
     FALLBACK_DIM detection as rag) and the CLI warns that only exact-match hits are
     reliable, with the concrete remedy (`ollama pull nomic-embed-text`). The provider
     hook with capability detection (probing multiple endpoints/models) remains open.
  3. **Cheap reranker:** optional cross-encoder rerank via the local engine for top-k.

## B. Semantic cache — correctness & cache-aware reuse — ✅ proposal (a)+(b) implemented

> **Status (Pass 174 + 178):** proposal (a) was already done before this audit —
> `aios_cache_tokens_saved_total` / `aios_cache_hits_total` / `aios_cache_cost_saved_usd_total`
> are all emitted in `metrics/prometheus.py`. Proposal (b)'s "per-model namespacing" was also
> already correct: `SemanticCache._key_hash` hashes only the model name, and every lookup
> query filters `WHERE key_hash = ?`, so a cached response for model A can never semantically
> match a lookup for model B (see `tests/test_new_features_178.py`). The one genuinely open
> piece — the cosine-similarity floor (`DEFAULT_THRESHOLD = 0.92`) had no user-facing knob —
> is now `Config.cache_similarity_floor`, settable via `aictl config set
> cache_similarity_floor <0-1>`, validated to (0.0, 1.0], read by `get_default_cache()` at
> first construction. No new CLI subcommand needed; reuses the existing generic `config set`
> mechanism.

- **Current (historical):** `core/sem_cache.py` keys on the same weak embedding; eviction is
  LRU by `last_hit_at` (now parameter-bound after the v1.6 fix). No notion of cache-aware RAG
  reuse or partial-prefix reuse.
- **SOTA:** CacheBlend / prefix-cache reuse for RAG contexts (scheduling survey); Portkey
  reports 30–50% cost cuts from semantic caching with <100ms hits.
- **Remaining gap:** cache-aware RAG reuse / partial-prefix reuse (CacheBlend-style) is not
  implemented — that piece of the SOTA gap is still open, unlike (a) and (b) above.

## C. Request routing — heuristic today, learned-optional tomorrow

- **Current:** `cmd/route.py:66` `score_complexity()` is a **hand-tuned regex/keyword scorer**
  (length, `_COMPLEX_PATTERNS`, `_CODE_PATTERNS`). README claims ~75% accuracy.
- **SOTA:** RouteLLM (arXiv:2406.18665) learns routers from preference data (BERT / matrix
  factorization / SW-ranking) for **up to 85% cost cut at 95% GPT-4 quality**; BEST-Route
  (arXiv:2506.22716) adds difficulty-aware multi-sampling (~60% cost, <1% quality drop);
  *Dynamic Model Routing & Cascading* survey (arXiv:2603.04445) and the router-robustness
  analysis (arXiv:2504.07113) warn heuristic routers are fragile.
- **Proposal (keeps zero-dep default):**
  1. **Embedding-kNN router:** route by nearest-neighbour over a small labeled prompt set
     (uses the engine's own embeddings; pure-Python kNN). Falls back to the regex scorer.
     Still open.
  2. **Cascade mode:** try the small model, escalate to the large model only if a local
     confidence/verifier check fails — a research-backed superset of the current one-shot route.
     ✅ **done** (`aictl route cascade`, `cmd/route.py`'s `run_cascade`).
  3. Ship a **router eval harness** so accuracy is a tracked number. ✅ **substantially done** —
     `aictl route test` (`cmd/route.py`'s `run_test`) runs a labeled 12-prompt test set through
     `score_complexity`/`classify_complexity` and reports accuracy (not literally an extension
     of `cmd/eval.py` as originally proposed, but satisfies the actual goal). A larger labeled
     set and eval.py integration would still be a nice-to-have, not a gap.

## D. Inference-engine coverage — three engines, the field has six — ✅ implemented (Pass 176)

> **Status:** `runtime/adapters.py` now has `LMDeployAdapter`, `TensorRTLLMAdapter`
> (`trtllm-serve`), and `LMStudioAdapter` — all OpenAI-compatible `/v1/*`, opt-in only
> (`EngineEndpoints.lmdeploy`/`tensorrt_llm`/`lm_studio` default `""`, excluded from
> `to_dict()` until configured via `aictl config set engines.<name> <url>`, so zero-config
> `discover_engines()`/`status`/demo/gate is unaffected). None has a documented Prometheus
> contract, so `scrape_metrics()` honestly returns basic status rather than guessing at
> metric names (same fallback `OllamaAdapter` already uses). MLX/Apple-Silicon detection
> was already done separately — see item I. `recommend`/`optimize`/`route` all consume
> `discover_engines()`/`get_adapter()` generically, so all three widen automatically with
> no further wiring.

- **Previously:** `runtime/adapters.py` detected only **vLLM / SGLang / Ollama**.
- **Peers:** 2026 comparisons treat **TensorRT-LLM, LMDeploy (TurboMind), LM Studio**, and
  **MLX on Apple Silicon** as mainstream; SGLang/LMDeploy show ~29% higher throughput than
  vLLM, TensorRT-LLM leads at scale, LMDeploy has lowest TTFT.

## E. KV-cache-aware cluster routing & long-context KV — proposal (a) done (Pass 177)

> **Status:** `SLOConfig.kv_cache_max` (default 0.9) was already threaded through
> `check_slo()` (governor) and `cmd/optimize.py`'s recommendations, but
> `runtime/router.py`'s `BrokerRouter` — the one component that actually decides where the
> next request goes — never referenced it; its only KV-awareness was a soft "headroom"
> factor in `_soft_score` that could still let a near-exhausted engine win. `BrokerRouter.route()`
> now hard-rejects any engine whose `kv_cache_utilization` exceeds `slo_target.kv_cache_max`
> (`kv_cache_exhausted (X% > Y%)`), the same way unreachable/wrong-status engines are
> already rejected. If every candidate is rejected this way, the existing priority-order
> `_fallback` path still returns a reachable engine (degraded, not a hard outage) — verified
> by test, not assumed.

- **Previously:** `runtime/prefix_route.py` does prefix-hash locality (RadixAttention-style),
  good. No KV-budget-aware scheduling or long-context KV compression advice.
- **SOTA:** *Online Scheduling with KV Cache Constraints* (arXiv:2502.07115); KV-cache
  optimization survey (eviction/compression: H2O, StreamingLLM, SnapKV); InfiniGen KV
  prefetch.
- **Proposal:** ~~(a) extend the router's soft-score with a KV-budget term~~ ✅ done
  (hard filter, not just soft-score — stronger than originally proposed). (b) add a
  **long-context KV advisor** to `cmd/optimize.py` recommending eviction/compression flags
  (e.g. vLLM cache settings) by context length — advisory-only, zero-dep. Still open.

## F. Prefill/decode & MoE serving advisors — ✅ implemented (v1.6)

> **Status:** shipped as `aictl deploy strategy <model> --gpu-count N --objective ...`
> (`cmd/deploy.py` + `runtime/serving_strategy.py`). Detects dense vs MoE, then recommends
> **aggregated (chunked-prefill)** vs **P/D-disaggregation** vs **AFD (Attention–FFN)** with
> ready-to-paste vLLM flags and the `aictl deploy disagg/optimize` command to materialize it.


- **Current:** `stack/disagg.py` already exports P/D-disaggregated manifests (NIXL/LMCache) and
  `runtime/dynamo.py` covers KVBM. Strong base.
- **SOTA (2025–26):** chunked-prefill + EP aggregation beats naive P/D for throughput
  (arXiv:2506.05508); **Attention–FFN disaggregation** (arXiv:2605.28302) wins on latency for
  MoE; intra-GPU P/D (arXiv:2507.06608); Mooncake/DeepSeek-V3 patterns.
- **Proposal:** add an **AFD / chunked-prefill advisor** to `deploy optimize`/`disagg` that,
  given model type (dense vs MoE), GPU count, and latency-vs-throughput objective, recommends
  P/D-disagg **vs** chunked-prefill-aggregation **vs** AFD, with ready-to-paste flags. This is
  exactly aictl's "advisor, not runtime" sweet spot.

## G. Guardrails — regex PII is table-stakes; harden against evasion

- **Current:** `core/guard.py` — 9 regex PII types + Luhn + 4 content policies, fully local.
  Good privacy story, but **single-turn, literal-match**.
- **SOTA / threat:** NeMo Guardrails + Presidio cover far more entity types and **output
  sanitization**; evasion research (arXiv:2504.11168) shows literal injection/jailbreak
  detectors hit **up to 100% bypass** via unicode/homoglyph/obfuscation, and multi-turn
  attacks evade single-turn checks.
- **Proposal (stdlib-friendly):**
  1. **Unicode normalization + homoglyph folding** before matching (NFKC, confusables) —
     closes the cheapest evasions, pure stdlib. ✅ **done** (earlier pass).
  2. **Output-side PII redaction** pass (not just input scan) and a redaction mode.
     ✅ **done (Pass 175)** — `core/guard.py` was previously a manual-only tool (`aictl
     guard scan`/MCP tool), never consulted on real inference traffic: a prompt-injection
     attempt sailed straight through the proxy, and PII an upstream model leaked in its
     response reached the client untouched. Two new `Config` fields
     (`guard_policy: off|warn|enforce`, `guard_redact_output: bool`, both default
     off/false — zero behavior change out of the box) now gate `aictl/daemon/proxy.py`:
     `_check_guard` (content-policy — injection/jailbreak/system-leak, request-side,
     before routing, both `/v1/chat/completions` and `/v1/embeddings`) and
     `_redact_response_pii` (PII redaction, response-side, non-streaming only — SSE has
     no buffering/reassembly point today, documented not silently dropped). Response
     redaction feeds the same `aios_guard_redactions_total` counter Pass 174 added, so
     real proxy traffic — not just the manual CLI — now shows up in the metric.
  3. **Optional model-based check hook** (Llama Guard via the local engine) behind a flag,
     keeping the regex layer as the zero-dep default. ✅ **done (Pass 179)** —
     `core/guard.py`'s `check_content()`/`scan()` gained an opt-in `model_check` parameter
     (a plain callable, never a global/auto-registered default) run in *addition* to the
     always-on regex rules. `make_llm_content_check(endpoint, model)` builds a real,
     zero-dep (urllib only) check that asks a local OpenAI-compatible chat endpoint (e.g.
     Ollama serving Llama Guard) for a SAFE/UNSAFE verdict; fails toward "no opinion" (an
     unreachable engine, timeout, malformed response, or non-http(s) scheme all return
     `None`) rather than raising or fabricating a block. Wired into both
     `aictl guard scan --model-check-endpoint/--model-check-model` and the proxy's
     `_check_guard` via two new `Config` fields (`guard_model_check_endpoint`, empty by
     default = disabled; `guard_model_check_model`).
  4. Multi-turn/session context for injection heuristics. Still open -- note: chat requests
     already concatenate every message in the request body's own `messages` array for
     scanning (`_extract_request_text`, Pass 175), so an attack spread across turns *within
     one request* (the client resends full history, as OpenAI-style APIs do) is already
     covered. The remaining gap is cross-request session state for callers that don't
     resend full history -- a genuinely separate, bigger feature (persisted per-session
     finding history), not done here.

## H. Quantization advisor — refresh to the 2026 frontier — ✅ implemented (Pass 180)

> **Status:** this doc's proposal was mostly already done and undocumented as such —
> `QUANT_DATA` (`cmd/quant.py`) already has an `"nvfp4"` row (4-bit float, Blackwell,
> `cc=100`, `q_chat=0.97`) and the AWQ row already notes "AutoAWQ is deprecated — export via
> llm-compressor/GPTQModel", both verified by reading the code, not assumed. The one
> genuinely missing piece — "surface the Q4_K_M sweet spot call-out in `quant recommend`" —
> is now `_q4_k_m_sweet_spot_note()`: whenever Q4_K_M fits the GPU/model but isn't the
> top-scored pick (e.g. NVFP4 wins on a Blackwell card), `quant recommend` calls it out as
> the portable, CPU-friendly fallback, in both human output and the `--json` body
> (`sweet_spot_note` field).

- **Current (historical):** `cmd/quant.py` advises FP16/FP8/Q8/AWQ/Q4/Q3 on "April 2026
  empirical" data.
- **Field:** Q4_K_M is the community sweet spot (92% quality / 75% smaller); **NVFP4 / MXFP4
  (4-bit float)** and updated AWQ/GPTQ kernels are now mainstream on Blackwell.

## I. Apple Silicon / unified-memory path — ✅ implemented (v1.6)

> **Status:** `runtime/broker.py` now has `detect_apple_silicon()` (M-series via
> sysctl), a `unified_memory` flag on `GPUInfo`, the pure `unified_memory_budget_mb()`
> helper (75% of RAM), an `apple-metal-<vram>` profile, and an MLX/Metal recommendation.
> `aictl fit --gpu "M3 Max"` reasons about unified memory via `lookup_apple_silicon_vram()`
> and the APPLE_SILICON_RAM_GB catalog, fixing the VRAM-only math that wrongly rejected
> large models on Macs (a 70B now correctly fits an M3 Max's 96GB budget).

- **Current:** profile detection is GPU/NPU/CPU; no MLX/Metal path. Peers (Ollama, LM Studio)
  moved to **MLX** on M-series as the faster default.
- **Proposal:** detect Apple Silicon + unified memory in `runtime/broker.py`/`recommend.py`,
  and have `fit`/`recommend` reason about **unified memory** (model can use system RAM as VRAM),
  which today's VRAM-only math gets wrong on Macs.

## J. Observability of the value props — ✅ implemented (v1.6)

> **Status:** the `/metrics` endpoint (`metrics/prometheus.py`) now emits value-prop counters
> peers (LiteLLM/Portkey/Helicone) expose: `aios_cache_tokens_saved_total`,
> `aios_cache_hits_total`, `aios_cache_cost_saved_usd_total`, `aios_cache_entries`,
> `aios_cache_hit_rate`, plus metering totals `aios_tokens_metered_total` /
> `aios_cost_metered_usd_total`, plus `aios_guard_redactions_total` (Pass 174 — lifetime
> PII items redacted via `aictl guard scan --redact`; `core/guard.py`'s `scan()` gained an
> opt-in `state_dir` kwarg so it stays a pure function everywhere else). All best-effort (a
> failed read never breaks `/metrics`), typed as Prometheus counters with the `_total`
> convention. Route-cost-saved still needs a baseline-cost methodology decision (saved vs.
> which alternative model?) before it can be a counter, and remains future work.

- **Current:** OTel GenAI spans + Prometheus exist (`metrics/`).
- **Gap:** the headline claims (cache savings, route savings, TCO) aren't all emitted as
  metrics. **Proposal:** emit `aios.cache.tokens_saved`, `aios.route.cost_saved`,
  `aios.guard.redactions` so dashboards prove the ROI competitors only assert.

---

## Suggested priority order

| Rank | Item | Why first | Effort |
|------|------|-----------|--------|
| 1 | **A** Hybrid retrieval + flag the hash fallback | Fixes a silent-quality-loss bug affecting RAG *and* cache; pure stdlib (BM25) | M |
| 2 | **G** Unicode-normalize guard inputs/outputs | Closes trivial PII/injection bypasses; small, pure stdlib | S |
| 3 | **C** Cascade routing + router eval harness | Direct cost/quality win, research-backed; reuses `eval.py` | M |
| 4 | **D** LMDeploy/TensorRT-LLM/MLX adapters | Broadens every advisor to the real engine field | M–L |
| 5 | **F** AFD / chunked-prefill advisor | High-value, fits the "advisor not runtime" model | M |
| 6 | **H** FP4 quant rows · **I** Apple unified memory · **J** ROI metrics | Incremental polish | S each |

Each lands as the project's standard flow: new/extended `cmd/*` or `runtime/*`, matching
tests under `tests/`, then `aictl gate`. None requires an external Python dependency.

---

## Sources

Inference engines: [Spheron](https://www.spheron.network/blog/vllm-vs-tensorrt-llm-vs-sglang-benchmarks/) ·
[Yotta Labs](https://www.yottalabs.ai/post/best-llm-inference-engines-in-2026-vllm-tensorrt-llm-tgi-and-sglang-compared) ·
[n1n.ai comparison](https://explore.n1n.ai/blog/llm-inference-engine-comparison-vllm-tgi-tensorrt-sglang-2026-03-13).
Gateways/routers: [PkgPulse](https://www.pkgpulse.com/guides/portkey-vs-litellm-vs-openrouter-llm-gateway-2026) ·
[Spheron AI Gateway](https://www.spheron.network/blog/ai-gateway-litellm-portkey-kong-gpu-cloud/).
Local tools: [Markaicode stacks](https://markaicode.com/best/best-local-llm-stack/) ·
[DEV local inference 2026](https://dev.to/starmorph/local-llm-inference-in-2026-the-complete-guide-to-tools-hardware-open-weight-models-2iho).
arXiv: [Taming the Titans survey 2504.19720](https://arxiv.org/abs/2504.19720) ·
[RouteLLM 2406.18665](https://arxiv.org/abs/2406.18665) ·
[BEST-Route 2506.22716](https://arxiv.org/abs/2506.22716) ·
[Routing/cascading survey 2603.04445](https://arxiv.org/html/2603.04445v2) ·
[Router robustness 2504.07113](https://arxiv.org/html/2504.07113v1) ·
[KV cache + scheduling 2502.07115](https://arxiv.org/html/2502.07115v5) ·
[Inference disaggregation 2506.05508](https://arxiv.org/html/2506.05508v1) ·
[Attention–FFN disaggregation 2605.28302](https://arxiv.org/html/2605.28302v1) ·
[Intra-GPU P/D 2507.06608](https://arxiv.org/html/2507.06608v4) ·
[Guardrail evasion 2504.11168](https://arxiv.org/abs/2504.11168).
Guardrails tooling: [NeMo Guardrails](https://github.com/NVIDIA-NeMo/Guardrails) ·
[Microsoft Presidio (via NeMo guide)](https://medium.com/@zbalogh/a-guide-to-setup-nemo-guardrails-to-instruct-aws-bedrocks-meta-llama-2-with-llama-guard-and-9bbefdc62ff3).

---

# Part 2 — second-pass gaps (structured decoding · spec-decode frontier · fairness/energy · agent interop)

A deeper sweep surfaced four more areas, each grounded in an existing module.

## K. Constrained / structured-decoding advisor — ✅ implemented (v1.6)

> **Status:** shipped as `aictl guided` (`cmd/guided.py` + `runtime/guided.py`) and the
> `aictl_guided` MCP tool. Provides the engine→backend support matrix
> (XGrammar/llguidance/Outlines/lm-format-enforcer) with ready-to-paste serve flags
> (`guided recommend`/`guided matrix`) **and** a dependency-free JSON-Schema validator
> (`guided validate`, also exposed to SDK/MCP callers via `runtime.guided.validate_json_schema`).

- **Current:** no guided-decoding support anywhere. aictl emits schema-shaped data
  everywhere (`--json` on every command, the JSON-RPC MCP server) yet offers no advice on
  *making the model* produce valid JSON.
- **SOTA:** **XGrammar** is the default structured-generation backend for **vLLM, SGLang and
  TensorRT-LLM as of 2026** (<40µs/token); **llguidance** (~50µs, Rust Earley), **Outlines**
  (regex→FSM), **Guidance** (~2× faster). JSONSchemaBench (arXiv:2501.10868) benchmarks six
  frameworks over 10K real schemas.
- **Proposal (zero-dep):** (1) add guided-decoding flags to `deploy optimize` with an
  **engine→backend support matrix** (XGrammar/Outlines/llguidance) and ready-to-paste serve
  flags (e.g. vLLM `--guided-decoding-backend xgrammar`); (2) a stdlib **JSON-Schema validator
  helper in `aictl/sdk.py`** so SDK callers can enforce/repair structured outputs locally;
  (3) optional schema enforcement on MCP tool results.

## L. Speculative-decoding advisor — ✅ implemented (earlier pass), Medusa still missing

> **Status:** this doc's "Current" bullet below was stale — `aictl spec methods` (`cmd/spec.py`,
> `runtime/speculative.py`) already implements the proposed method dimension: `none | eagle3 |
> p-eagle | mtp | ngram | standalone`, each with an engine-support matrix and ready-to-paste
> flags (e.g. vLLM `--speculative-config '{"method":"eagle3",...}'`). `aictl spec methods --all`
> shows the full matrix. **Medusa specifically is not one of the modeled methods** — the one
> sub-item from the original proposal genuinely still open.

- **Previously assumed:** `cmd/spec.py` only paired a **draft + target model** (classic spec
  decoding); no EAGLE/Medusa/MTP awareness. Verified false by reading the actual code.
- **SOTA:** **EAGLE-3** (arXiv:2503.01840) is now the **de-facto industrial standard** — up to
  **4.79× on Llama-3.3-70B with no quality loss**, supported by vLLM and SGLang; **Medusa**
  (multi-head) and **DeepSeek-V3 multi-token-prediction (MTP)** are mainstream; SpecForge
  (2603.18567) trains drafters.

## M. Fairness & carbon/energy-aware scheduling — carbon advisor ✅ implemented (v1.6); fair-share TBD

> **Carbon/energy advisor status:** shipped as `aictl tco carbon` and `aictl tco --carbon-intensity`.
> Shows kWh + CO₂e (regional IEA 2024 grid intensities), GPU power-cap flags (`nvidia-smi -pl`),
> projected savings, and FREESH-style scheduling projections (28.6% energy / 45.5% emissions).
> The `aictl_tco` MCP tool now also accepts `region` / `carbon_intensity`. The VTC/DLPM
> fair-share scheduling component remains a future item.

## M original. Fairness & carbon/energy-aware scheduling — counters exist, policy doesn't

- **Current:** aictl has per-tenant **metering** (`core/metering.py`), **tenant** quotas
  (`core/tenant.py`), an **autoscaler** (`runtime/autoscaler.py`) and a **governor**
  (`daemon/governor.py`) — but no fair-share scheduling and **no energy/carbon objective**
  (confirmed: `cmd/tco.py` has no carbon/energy term).
- **SOTA:** **VTC** fair scheduling; **Equinox** holistic fairness (arXiv:2508.16646, worst-case
  service-gap −42%, avg −86% vs VTC); **DLPM** locality-aware fair scheduling that preserves
  prefix locality (arXiv:2501.14312); **FREESH** (arXiv:2511.00807) cuts **28.6% energy /
  45.5% emissions** via Least-Laxity-First + dynamic GPU-frequency scaling.
- **Proposal (zero-dep, advisory-first):** (1) a **VTC/DLPM-style fair-share counter** in the
  governor/router that blends the token usage aictl already meters with prefix-locality from
  `prefix_route.py`; (2) a **carbon/energy advisor in `cmd/tco.py`** — accept a carbon-intensity
  input, recommend **GPU power-cap / frequency-scaling** settings and report kWh + CO₂e
  alongside dollars, turning TCO into TCO+carbon.

## N. MCP server & agent interoperability — observability/streaming gap

- **Current:** a 19-tool JSON-RPC MCP server (`aictl/mcp_server`). Proposal (1) below is
  already done — this doc previously claimed otherwise; verified false by reading the code:
  `mcp_server.py`'s `handle_tool()` wraps every tool dispatch in a `ToolSpan`
  (`metrics/genai_spans.py`), ring-buffered and fire-and-forget OTLP-exported when
  `AIOS_OTEL_ENDPOINT` is set, with observability failures never propagating to the caller.
  Proposals (2) streaming/progress notifications and (3) session persistence remain genuinely
  open.
- **Field:** the 2026 agent frameworks (Claude Agent SDK, LangGraph, OpenAI Agents SDK, AWS
  **Strands**) all converge on **MCP + OTel + streaming + persistence**; Strands in particular
  plugs straight into any OTel backend. Observability is now the differentiator, not tool count.
- **Proposal:** ~~(1) emit an OTel span per MCP tool call~~ ✅ done. (2) add
  **streaming/progress notifications** for long tools (deploy, bench); (3) optional
  **session persistence** for multi-step agent flows.

## Updated priority (Parts 1 + 2)

After the two passes, the cheapest high-impact wins are still **A** (hybrid retrieval) and
**G** (guard unicode-hardening); **K** (guided-decoding advisor) and **L** (EAGLE-3 in the
spec advisor) join the front of the queue because both are **pure advisory + flag-emission**
work that fits aictl's model perfectly and needs no runtime or external dep. **M** and **N**
are larger but leverage infrastructure aictl already owns (metering, OTel).

## Sources (Part 2)

Structured/constrained decoding: [JSONSchemaBench 2501.10868](https://arxiv.org/html/2501.10868v1) ·
[Awesome-Constrained-Decoding](https://github.com/Saibo-creator/Awesome-LLM-Constrained-Decoding) ·
[structured-output guide 2026](https://collinwilkins.com/articles/structured-output).
Speculative decoding: [EAGLE-3 2503.01840](https://arxiv.org/html/2503.01840v1) ·
[SpecForge 2603.18567](https://arxiv.org/pdf/2603.18567).
Fairness/energy scheduling: [Equinox 2508.16646](https://arxiv.org/html/2508.16646v1) ·
[DLPM locality-aware fair 2501.14312](https://arxiv.org/html/2501.14312v1) ·
[FREESH 2511.00807](https://arxiv.org/pdf/2511.00807).
Agent frameworks: [2026 framework showdown](https://qubittool.com/blog/ai-agent-framework-comparison-2026) ·
[8 SDKs / ACP trade-offs](https://www.morphllm.com/ai-agent-framework).

---

# Part 3 — July 2026 research refresh

Fresh survey (July 2026) of papers, engine releases, and protocol news, each
finding grep-verified against the codebase before being listed (same
discipline as Parts 1–2: no assumed gaps).

## O. MCP spec 2026-07-28 — stateless core — ✅ implemented (Pass 182)

> **Status:** `initialize` is kept for legacy clients but now negotiates the
> requested `protocolVersion` (echoes back 2024-11-05 / 2025-06-18 / 2026-07-28
> if the client asks for one of those; falls back to the 2024-11-05 default
> otherwise — zero behavior change for any pre-existing caller). New
> `server/discover` method is the RC's stateless replacement, callable with
> no prior handshake. `tools/list` gained `ttlMs`/`cacheScope`. `_meta` in
> params was already tolerated (unknown keys are simply ignored) — pinned
> with a regression test rather than left as an untested assumption. The
> Tasks extension migration is deliberately deferred until the final spec +
> SDK ecosystem settle (per the original proposal).

- **Historical current:** `aictl/mcp_server.py:47` advertised `protocolVersion:
  "2024-11-05"` — two spec generations old — and implemented only the legacy
  `initialize` handshake. The server was already internally stateless (no
  session tracking), so migration cost was low.
- **News:** the 2026-07-28 release candidate removes the `initialize`
  handshake and `Mcp-Session-Id` (client metadata moves to `_meta` on every
  request), adds `server/discover`, moves Tasks to an extension, deprecates
  roots/sampling/logging, and changes some error codes (-32002 → -32602 for
  missing resources; aictl already emitted -32601/-32602 only — nothing to
  migrate there, confirmed by grep). Final spec ships July 28, 2026.
- **Proposal (done):** dual-mode compatibility — keep `initialize` for legacy
  clients but negotiate the requested protocol version; add
  `server/discover`; tolerate per-request `_meta`; add `ttlMs`/`cacheScope`
  to `tools/list`. Defer the Tasks extension until the final spec + SDKs
  settle.

## P. Guardrail-as-DoS-target — cache the model-check verdicts — ✅ implemented (Pass 183)

> **Status:** both proposals shipped. (1) `make_llm_content_check`'s callable
> consults a module-level thread-safe LRU verdict cache
> (`GUARD_MODEL_CHECK_CACHE_MAX_ENTRIES` = 256, keyed SHA-256 of
> endpoint|model|Unicode-normalized text) before any network call — module-level
> rather than closure-local because the proxy constructs a fresh closure per
> request (config re-read per request), so a closure-local cache would never be
> reused. Only genuine SAFE/UNSAFE classifications are cached; network failures
> are NOT, so a transient outage can't get stuck as permanent no-opinion.
> (2) `check_content()` skips the model check entirely when the regex layer
> already found a blocking violation — an obviously-malicious flood never
> reaches the upstream model at all. E2E-verified through the real proxy:
> 5 identical requests → exactly 1 upstream guard-model call.

- **Historical current:** Pass 179's `make_llm_content_check` (core/guard.py)
  ran a synchronous LLM call per guarded request with NO verdict caching —
  a flood of identical prompts re-triggered the upstream model every time.
- **SOTA / threat:** "From Shield to Target" (arXiv:2606.14517, June 2026)
  characterizes LLM-based guardrails as resource-amplification DoS targets.

## Q. vLLM v0.19 features the advisors don't mention yet — ✅ fit hint done (Pass 184)

> **Status:** `aictl fit`'s doesn't-fit path now appends a note pointing at
> vLLM v0.19+ CPU KV-cache offloading (spill KV to system RAM) as a remedy
> alongside the existing quantization/alternative-model suggestions — scoped
> to the "weights fit but context doesn't" case, honestly noting the latency
> cost. `optimize` flag emission for offloading remains open (needs the
> exact flag surface to settle across vLLM point releases before we emit
> ready-to-paste flags).

- **Current (historical):** aictl pins `vllm/vllm-openai:v0.19.0`
  (constants.py:65). That release shipped CPU KV-cache offloading (serve
  models bigger than VRAM by spilling KV to system RAM), FlexKV, and
  zero-bubble async-scheduled speculative decoding — none surfaced by
  `aictl fit`/`optimize` advisories.

## R. Model catalog drift (June–July 2026 releases) — ✅ implemented (Pass 184)

> **Status:** GLM-5.2 added as both `glm5.2:9b` (ollama, q4_K_M) and
> `zai-org/GLM-5.2` (vllm, fp8 flagship); Kimi K2.6 added as `kimi-k2.6`
> (ollama, 1T MoE / 32B active — VRAM sized like the existing DeepSeek-V4
> 32B-active precedent). MODELS 34 → 37; the count-sync test
> (test_category_audit_fixes_32.py, now a single EXPECTED pin) and
> runtime/CLAUDE.md move together with it. Medusa is now a modeled method
> in `runtime/speculative.py` + `cmd/spec.py`'s matrix (vLLM + TRT-LLM,
> trained heads required, note steers to EAGLE-3 where a head exists;
> auto_select_method deliberately never picks it) — closes item L's last
> gap. Dynamo v0.8→1.0 GA text was fixed in Pass 181.

## S. Layered routing + local embeddings are now settled practice

- **News:** RouteJudge (arXiv:2606.18774), cascade decision theory
  (arXiv:2605.06350), and the 2026 routing surveys converge on a 3-layer
  pattern: cheap rules → embedding/kNN middle layer → cascade tail. aictl
  has layers 1 and 3; the embedding middle layer is backlog item C-1.
  For item A-2 (pluggable real embeddings), the 2026 local consensus picks
  are now concrete: nomic-embed-text (137M), Qwen3-Embedding-0.6B, bge-small
  — all Ollama-runnable; rerank with BGE-reranker-v2/Qwen3-Reranker.
- **Proposal:** unchanged from C-1/A-2, now with named models and validated
  architecture; kNN router should build on the embedding provider hook.

## Sources (Part 3)

MCP 2026-07-28 RC: [official RC post](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/).
Guardrail DoS: [From Shield to Target 2606.14517](https://arxiv.org/abs/2606.14517).
vLLM: [releases](https://github.com/vllm-project/vllm/releases) ·
[Q2 2026 roadmap #39749](https://github.com/vllm-project/vllm/issues/39749) ·
[CPU offloading write-up](https://aiforautomation.io/news/2026-04-02-vllm-cpu-offloading-run-bigger-models-free).
Routing: [RouteJudge 2606.18774](https://arxiv.org/pdf/2606.18774) ·
[cascade decision theory 2605.06350](https://arxiv.org/pdf/2605.06350) ·
[2026 routing guide](https://www.digitalapplied.com/blog/llm-model-routing-2026-cost-quality-optimization-engineering-guide).
Embeddings: [Milvus 2026 comparison](https://milvus.io/blog/choose-embedding-model-rag-2026.md) ·
[BentoML open-source guide](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models) ·
[Qwen3 embed/rerank via Ollama](https://apidog.com/blog/qwen-3-embedding-reranker-ollama/).
Engines/models: [engine comparison 2026](https://leetllm.com/blog/llm-inference-engine-comparison-2026) ·
[Ollama release notes](https://releasebot.io/updates/ollama).
Spec-decode attack (context only): Mistletoe acceleration-collapse (arXiv:2605.14005).
