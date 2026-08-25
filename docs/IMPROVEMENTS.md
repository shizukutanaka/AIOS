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

## A. RAG quality — the embedding is the weak link  ⭐ highest leverage — proposals 2+3 ✅ done (Pass 186, 189)

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
     reliable, with the concrete remedy (`ollama pull nomic-embed-text`).
     **Capability-detection half ✅ done (Pass 186):** `sdk._detect_embedding_model`
     probes the engine's `/v1/models` (the OpenAI-compatible listing every adapter in
     this project already speaks) and picks the best available embedding-capable model
     from the 2026-consensus priority list (nomic-embed-text > bge-m3 >
     qwen3-embedding > bge-large > bge-small > all-minilm) instead of blindly guessing
     "nomic-embed-text" on every call. No match → skips the doomed `/v1/embeddings`
     POST entirely and degrades straight to the hash fallback. Cached per-endpoint for
     the process lifetime (embed_text is hot-path: every cache lookup/store, every RAG
     query) — matches `_AmbientContext`'s own detect-once convention. Proposal 2 is now
     fully closed.
  3. **Cheap reranker:** optional cross-encoder rerank via the local engine for top-k.
     ✅ **done (Pass 189)** — research (a dedicated Workflow pass) found Ollama has no
     native rerank endpoint at all, and while vLLM claims Cohere-compatibility for its own
     `/rerank`, the exact field names couldn't be independently confirmed (vLLM's own docs
     403'd automated fetches three times). TEI (HuggingFace Text Embeddings Inference)'s
     `/rerank` contract was the only one verified against its own OpenAPI spec, so
     `core/rerank.py`'s `rerank(endpoint, model, query, candidates)` targets that shape
     exclusively rather than shipping a guessed vLLM/Cohere contract. Off by default
     (`Config.rerank_endpoint == ""` — zero network calls, RRF order unchanged);
     `core/rag.py`'s `search()`/`answer()` gained an optional `config` parameter that,
     when `rerank_endpoint` is set, reranks a widened RRF-fused candidate pool
     (`max(k*4, RERANK_POOL_MIN=20)`, not just the naive top-k — reranking only the
     already-sliced top-k would defeat the purpose) before the final `[:k]` slice. Any
     failure (unreachable endpoint, non-2xx, malformed JSON, out-of-range index)
     silently falls back to the pre-existing RRF order. Wired into `rag search`/`rag ask`
     via a `--rerank` flag; `--rerank` without a configured `rerank_endpoint` warns once
     and proceeds unaffected rather than silently no-op'ing. 26 new tests
     (`tests/test_new_features_189.py`).

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
     ✅ **done (Pass 187)** — `cmd/route.py`'s `route_tier_gated()`. Design: **confidence-gated
     tie-breaker**, not a replacement — `score_complexity`/`classify_complexity` always run
     first and are the sole verdict unless (a) `route_knn_enabled` is set (default `False`,
     true no-op: zero `embed_text()` calls, zero disk I/O) or `--knn`/`force=True` is passed,
     (b) the regex score falls within `route_knn_margin` (default 5) of the 30/60 tier
     boundary — i.e. this is a genuine toss-up, not a clear call, (c) the 30-example labeled
     bank (`_KNN_EXAMPLES`, 10/tier, disjoint from the 12-entry `_TEST_CASES` eval set) has
     real (non-hash-fallback) embeddings, disk+memory cached in `route_knn_cache.json`
     (hash-invalidated on edits to `_KNN_EXAMPLES`, self-heals by retrying the embed after an
     hour if the last attempt degraded to the hash fallback), (d) the live query's own
     embedding is also real, (e) a `heapq.nlargest(k, ...)` neighbor vote clears
     `route_knn_min_agreement` (default 0.8), and (f) the kNN verdict is an **adjacent tier
     only** — a SIMPLE→COMPLEX 2-tier jump is always rejected regardless of agreement. Any
     exception anywhere silently abstains to the regex verdict. Wired into `route show`/`ask`/
     `test` via a `--knn` flag (JSON gains an additive `knn_applied` key; existing keys
     unchanged); `route batch`/`cascade` deliberately **not** wired this pass (latency/
     throughput-sensitive paths — left as an explicit, documented gap rather than a silent
     omission). 29 new tests (`tests/test_new_features_187.py`).
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

## M. Fairness & carbon/energy-aware scheduling — carbon advisor ✅ (v1.6); fair-share advisory ✅ (Pass 190)

> **Carbon/energy advisor status:** shipped as `aictl tco carbon` and `aictl tco --carbon-intensity`.
> Shows kWh + CO₂e (regional IEA 2024 grid intensities), GPU power-cap flags (`nvidia-smi -pl`),
> projected savings, and FREESH-style scheduling projections (28.6% energy / 45.5% emissions).
> The `aictl_tco` MCP tool now also accepts `region` / `carbon_intensity`.

> **Fair-share advisory status (Pass 190):** research (a dedicated Workflow) found VTC's
> (arXiv:2401.00588) exact weighted virtual-counter formula could not be independently verified
> (the paper PDF itself was unreachable to the fetcher) — rather than ship a guessed formula,
> `aictl tco fairshare` reports **Jain's Fairness Index** over each metered entity's share of
> `core/metering.py`'s existing cumulative `total_tokens` (the well-grounded, verifiable
> alternative the research surfaced; needs no new counters). `core/fairness.py`'s
> `compute_fairness()` is a pure function over `TokenMeter.list_usage()` — advisory only, no
> code path in `governor.py`/`broker.py`/the request admission path touches it, matching the
> "advisory-first" scoping explicitly called for below. Each entity is classified `starved` /
> `fair` / `over_share` against `1/n` expected share (with the honest, tested property that
> `over_share` is mathematically unreachable at exactly 2 entities — `share > 2/n` requires
> `share > 1.0` when `n=2`). DLPM's locality-blending (see proposal 1 below) was explicitly
> **not** attempted this pass: research confirmed `runtime/prefix_route.py`'s
> `PrefixRouteTracker` is endpoint-keyed only with no per-tenant/entity dimension anywhere in
> it, so blending it in would mean fabricating data that doesn't exist — the report instead
> carries an honest `locality_note` documenting this as a named future extension. A live VTC/
> DLPM scheduler wired into the governor/router's actual admission path (rather than a report)
> remains open — a genuinely bigger feature, since `governor.py` was confirmed to have no
> per-tenant concept anywhere today (purely SLO-reactive on engines, not entities). 16 new tests
> (`tests/test_new_features_190.py`).

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
  `prefix_route.py` — **advisory report half ✅ done (Pass 190, see above)**; a live scheduler
  wired into the actual admission path remains open. (2) a **carbon/energy advisor in
  `cmd/tco.py`** — accept a carbon-intensity input, recommend **GPU power-cap /
  frequency-scaling** settings and report kWh + CO₂e alongside dollars, turning TCO into
  TCO+carbon. ✅ **done** (`aictl tco carbon`, see above).

## N. MCP server & agent interoperability — observability ✅ + streaming ✅ (Pass 188); session persistence open

- **Current:** a 19-tool JSON-RPC MCP server (`aictl/mcp_server`). Proposal (1) below is
  already done — this doc previously claimed otherwise; verified false by reading the code:
  `mcp_server.py`'s `handle_tool()` wraps every tool dispatch in a `ToolSpan`
  (`metrics/genai_spans.py`), ring-buffered and fire-and-forget OTLP-exported when
  `AIOS_OTEL_ENDPOINT` is set, with observability failures never propagating to the caller.
  Proposal (2) is now done (Pass 188); (3) session persistence remains genuinely open.
- **Field:** the 2026 agent frameworks (Claude Agent SDK, LangGraph, OpenAI Agents SDK, AWS
  **Strands**) all converge on **MCP + OTel + streaming + persistence**; Strands in particular
  plugs straight into any OTel backend. Observability is now the differentiator, not tool count.
- **Proposal:** ~~(1) emit an OTel span per MCP tool call~~ ✅ done. (2) add
  **streaming/progress notifications** for long tools (deploy, bench); ✅ **done (Pass 188)** —
  research (canonical MCP GitHub source: `schema.ts` + `progress.mdx` across 2024-11-05 through
  the 2026-07-28 RC draft, since the docs site 403s automated fetches) confirmed
  `params._meta.progressToken` is stable across every spec version, `notifications/progress`
  gained an optional `message` field in 2025-03-26+, and no `capabilities.progress` entry exists
  in any `ClientCapabilities`/`ServerCapabilities` schema — support is implicit/opt-in per
  request, not capability-negotiated. Of the 19 tools, only `aictl_eval` has genuine multi-step
  latency (a real `aictl.ai.ask()` call per case via `_run_case`); it's the sole tool
  instrumented this pass — over-instrumenting a sub-millisecond tool would just be noise.
  `handle_request`'s `tools/call` branch extracts `progress_token` from
  `params._meta.progressToken`; `handle_tool`/`_dispatch_tool` thread it through as an optional
  `on_progress`/`progress_token` parameter (default `None`, so every pre-existing call site and
  every other tool is unaffected); `_make_progress_emitter()` builds a callback that writes a
  spec-shaped `notifications/progress` line to stdout and flushes it (the stdio loop is
  single-threaded/blocking per request, so no other writer can race it), wrapped in the same
  "observability must never break the serving path" `try/except Exception: pass` convention
  `handle_tool`'s `ToolSpan` export already uses — doubled up with a second guard around the
  call site inside `_tool_eval` itself, so even a misbehaving `on_progress` callback (not just a
  broken stdout pipe) can't abort an eval run. `initialize`/`server/discover` both gained an
  (optional, presence-only) `"progress": {}` capability entry. 14 new tests
  (`tests/test_new_features_188.py`). (3) optional **session persistence** for multi-step agent
  flows remains open — a genuinely bigger feature (this server is intentionally stateless today).

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

## S. Layered routing + local embeddings are now settled practice — ✅ layer 2 done (Pass 187)

- **News:** RouteJudge (arXiv:2606.18774), cascade decision theory
  (arXiv:2605.06350), and the 2026 routing surveys converge on a 3-layer
  pattern: cheap rules → embedding/kNN middle layer → cascade tail. aictl
  now has all three layers: rules (`score_complexity`), embedding/kNN
  (`route_tier_gated`, item C-1, ✅ Pass 187), cascade (`run_cascade`).
  For item A-2 (pluggable real embeddings), the 2026 local consensus picks
  are now concrete: nomic-embed-text (137M), Qwen3-Embedding-0.6B, bge-small
  — all Ollama-runnable; rerank with BGE-reranker-v2/Qwen3-Reranker.
- **Proposal:** ~~unchanged from C-1/A-2, now with named models and validated
  architecture; kNN router should build on the embedding provider hook.~~
  Done — the kNN router (C-1) builds directly on the Pass 186 embedding
  capability-detection hook (A-2's remaining half). ~~A reranker (Qwen3-
  Reranker/BGE-reranker-v2) for `rag search` result re-ordering remains a
  separate, still-open nice-to-have, not part of the routing layer.~~ Also
  done (Pass 189, item A proposal 3) — `core/rerank.py`, off by default,
  targets a TEI-compatible `/rerank` endpoint (see item A above for why TEI
  over vLLM/Cohere's contract).

## T. Engine conformance — the dependency nothing verified — ✅ implemented (Pass 191)

> **Status:** shipped as `aictl engines conform [url]` (`runtime/conformance.py`).

- **How it was found:** a First-Principles pass mapped all 80 commands onto the
  irreducible chain "run local AI inference well" reduces to (know hardware →
  get model → run it → talk to it → know it works → know cost → keep safe →
  improve quality). Every step had commands; one *precondition* had none.
- **The gap (verified, not assumed):** aictl's entire value rests on an engine
  behaving as expected, yet nothing checked that. `cmd/selftest.py` contains
  zero endpoint/`urlopen`/`/v1/` references — it never contacts an engine. The
  3451-test suite and `aictl demo` exercise only the bundled mock engine, which
  proves internal consistency, not that a *user's* engine speaks what aictl
  needs. Non-conformance therefore surfaced mid-request as silent quality loss:
  an engine without `/v1/embeddings` makes `rag`/`cache`/`route --knn` fall back
  to the non-semantic SHA-256 hash embedding, with retrieval quietly ceasing to
  be semantic.
- **Implementation:** six probes (`/v1/models`, reachability, chat completions,
  streaming, `/v1/embeddings`, `/metrics`), each classified `required` /
  `degraded` / `optional` and — the actual point — **mapped to the aictl
  features it powers**, so a missing surface reads as "rag and cache lose
  semantic search" rather than "404". Read-only (GETs plus 1-token POSTs), never
  raises (an unreachable engine still yields the full 6-probe report so `--json`
  consumers get a stable shape), `--json` supported, exit code stays 0 unless
  `--strict`.
- **Scoping note:** added as a subcommand of the existing `engines` command, not
  an 81st top-level command — the same First-Principles pass identified command
  sprawl (10 observability entry points, 9 advisory-only commands) as the
  project's main *excess*, so growing that surface to fix a gap would have been
  self-defeating. See `docs/REVIEW_v1.7.0.md`.
- **Validation:** verified end-to-end against the project's own `mock_engine` (a
  real HTTP server, not a hand-rolled double), which genuinely lacks
  `/v1/embeddings` — making it a live demonstration of the degradation case the
  feature exists to surface. 18 new tests (`tests/test_new_features_191.py`).
- **Still open:** the probes describe conformance, they do not yet *gate*
  anything — e.g. `rag index` could warn up front when the configured engine
  fails the embeddings probe. Deliberately deferred rather than silently
  skipped.

## U. KV prefix-cache offload advice (OffloadingConnector) — ✅ implemented (Pass 192)

> **Status:** shipped as `optimize_vllm_flags(..., enable_kv_offload=True)`
> (`runtime/kv_offload.py`). Off by default.

- **The gap:** `optimize_vllm_flags` has always emitted `--enable-prefix-caching`,
  but that cache lives in whatever VRAM the weights left behind. On a large
  model / small GPU the leftover is thin enough to thrash, so on prefix-heavy
  workloads — multi-turn chat, RAG sharing one system prompt, agent loops
  replaying a transcript — the reuse the flag promises never materializes.
  aictl had no notion of the fix.
- **Upstream mechanism:** vLLM's OffloadingConnector (2026) extends the prefix
  cache into pinned host memory: completed GPU blocks are DMA'd out to a CPU
  tier instead of being dropped, so an evicted prefix stays a cache hit rather
  than a recompute.
- **Schema discipline:** the emitted `--kv-transfer-config` uses only keys
  verified against the connector's own pull request (vllm-project/vllm#24498) —
  `kv_connector` / `kv_role` / `kv_connector_extra_config.{block_size,
  cpu_bytes_to_use}`, bytes as the unit, replacing the legacy `num_cpu_blocks`.
  `spec_name` and the multi-tier options appear in secondary sources but the
  primary docs were unreachable from this environment (docs.vllm.ai and
  vllm.ai are egress-blocked), so they are deliberately **not** emitted, and a
  test pins that boundary. One verified contract over two guessed ones.
- **Safety property, not a preference:** `cpu_bytes_to_use` is *pinned*
  (page-locked) host memory — unswappable and gone from the OS for the
  engine's lifetime, so over-allocating it degrades the whole host rather than
  just the engine. Sizing takes at most 25% of host RAM, always leaves an 8GB
  floor, returns nothing rather than a token allocation below 4GiB, and
  **refuses outright when host RAM is unknown** instead of guessing.
- **Honest scoping:** the advisor declines, with a reason, when the GPU KV
  cache already dwarfs any safe host tier (a small model on an 80GB card), on
  non-GPU hosts, and when measured prefix reuse is under 10% — offloading buys
  prefix hits and nothing else. Notes state plainly that it does *not* make a
  model that exceeds VRAM fit, the most likely misreading.
- **Measurement over heuristic:** an optional `prefix_reuse` argument (aictl's
  `PrefixRouteTracker` already produces one) overrides the heuristic in both
  directions when supplied.
- **Validation:** 31 new tests (`tests/test_new_features_192.py`), including
  that the default path is byte-identical to before and that enabling adds
  exactly one flag.
- **Follow-up closed in Pass 193 (below):** the `prefix_reuse` argument is now
  supplied automatically from the router's own measurements. A CLI flag
  exposing `enable_kv_offload` is still not wired.

## V. Measured prefix reuse feeds the offload decision — ✅ implemented (Pass 193)

> **Status:** `PrefixRouteTracker.reuse_rate()` → `measured_prefix_reuse()` →
> `advise_kv_offload`. Closes item U's documented follow-up.

- **The gap:** item U accepted a `prefix_reuse` measurement but nothing
  produced one, so it always fell back to *assuming* a prefix-heavy workload.
  Meanwhile `PrefixRouteTracker.best_endpoint()` was answering exactly that
  question on every request — "does a warm prefix exist for this prompt?" —
  and discarding the answer. The measurement was already being computed and
  thrown away.
- **Grounding:** KVFlow (NeurIPS 2025, arXiv:2507.07400) finds LRU eviction is
  fundamentally mismatched with agentic workflows: it evicts on past access
  time while the workflow structure already encodes future execution order, so
  caches are dropped shortly before reuse. aictl cannot change an engine's
  eviction policy, but that is precisely the regime where enlarging the cache
  tier recovers hits eviction would otherwise squander. Whether a deployment
  is in that regime is empirical — and is now measured rather than assumed.
- **Implementation:** hit/miss counters on the tracker (inside the existing
  lock, counted only for lookups that actually consulted history — malformed
  queries are not evidence), `reuse_rate()`, counters surfaced in `stats()`,
  and reset by `clear()`.
- **The load-bearing distinction:** `reuse_rate()` returns `None` when nothing
  has been observed and `0.0` when reuse was observed to be absent. Callers
  act on these in *opposite* directions — None defers to the heuristic, 0.0
  vetoes offloading — so collapsing them (e.g. `prefix_reuse or measured()`)
  would silently turn "no data yet" into "don't bother". A regression test
  pins it.
- **Known caveat, documented not hidden:** `advise_kv_offload` now reads
  process-global state when `prefix_reuse` is omitted, so identical arguments
  can yield different advice in a process that has served traffic versus a
  fresh one. That is the intent (an observed workload beats an assumed one);
  callers wanting ambient-independent advice pass `prefix_reuse` explicitly.
  Verified deliberately: a process fed 30 one-shot prompts correctly declines
  offloading.
- **Validation:** 19 new tests (`tests/test_new_features_193.py`), including
  thread-safety of the counters under 8 concurrent readers, that routing
  decisions are unchanged by the accounting, and that the suite passes in both
  file orders (the new global-state read makes order-dependence a real risk).
- **Follow-up closed in Pass 194 (below):** `--kv-offload` now exposes it.
  The reuse rate remains process-local and resets on restart.

## W. `--kv-offload` on the CLI, and a vendor-gating bug it exposed — ✅ implemented (Pass 194)

> **Status:** `aictl deploy optimize <model> --kv-offload [--host-ram MB]`.
> Closes item U's remaining follow-up.

- **The gap:** items U and V built the advisor and gave it a real measurement,
  but `enable_kv_offload` was reachable only from Python — no user could get
  at it.
- **Detection had to change shape:** sizing depends on *host* RAM, which the
  existing auto-detect path never needed (it only looked at GPUs) and only ran
  under `--gpu auto`. `full_detect()` is now also triggered by `--kv-offload`,
  probed exactly once and reused — with a test asserting it is not run at all
  when both GPU and host RAM are given, since it shells out to
  nvidia-smi/rocm-smi.
- **Real bug found by a test, not by review:** `HardwareProfile.vendor`
  defaulted to `"nvidia"` for every explicitly-named GPU, so
  `--gpu CPU --kv-offload` recommended offloading from a device that wasn't
  there — the advisor's vendor gate was being fed a wrong constant. Fixed with
  `_infer_vendor()`, which maps CPU/none to `"cpu"` and recognizes AMD/Intel/
  Apple names, while keeping the historical `"nvidia"` assumption for unknown
  names (better to keep advising for a GPU missing from the tables than to
  silently disable features for it).
- **Advisory semantics preserved:** declining is not an error — exit stays 0
  and the reason is printed, so `--kv-offload` on an unsuitable host is
  informative rather than a failure.
- **Validation:** 18 new tests (`tests/test_new_features_194.py`), including
  that the default path emits nothing, `--json` stays valid, explicit
  `--host-ram` overrides detection (the machine generating flags is often not
  the machine that will run the engine), and old Namespaces without the new
  attributes still work.
- **Follow-up closed in Pass 195 (below):** the reuse rate now persists, so a
  fresh CLI run can read what the daemon measured.

## X. Persisted prefix reuse, and three bugs the tests found — ✅ implemented (Pass 195)

> **Status:** `PrefixRouteTracker.flush_reuse()` / `persisted_reuse_rate()`,
> opt-in per tracker, enabled by the daemon. Closes item W's limitation.

- **The gap:** the reuse rate was process-local, so `deploy optimize
  --kv-offload` — a fresh process that has served no traffic — always fell
  back to the heuristic. The measurement only accumulated in the long-lived
  daemon, which is precisely the process that never asks for the advice.
- **Design:** the log stores *deltas*, not absolute counts. Appends under
  PIPE_BUF are atomic on POSIX, so concurrent writers share one file without
  locking and a reader simply sums; absolute counts would need
  read-modify-write and would race. Mirrors `core/perf.py`'s rationale.
- **Staleness rule:** records older than 24h are ignored, and untimestamped
  ones (predating the `ts` field) with them. Advice driven by month-old
  traffic is worse than advice that admits it has no data — the workload it
  measured may no longer exist.

**Three real bugs, each caught by a test or an inspection rather than review:**

1. **Non-dict JSON crashed the reader.** A well-formed line that wasn't an
   object (`["wrong","shape"]`) raised `AttributeError` on `.get`, which the
   handler didn't catch — it escaped and would crash the caller. Fixed with an
   `isinstance` check in both readers.
2. **Concurrent flushes double-counted.** The delta was computed under the
   lock, the lock released for the write, then the cursor advanced — so two
   threads could claim overlapping ranges and write the same lookups twice.
   Observed as 1212 persisted vs 1200 actual under 6 threads. Fixed by
   reserving *and* claiming the delta in one atomic step, un-claiming on write
   failure so counts are retried rather than lost.
3. **A process-global opt-in leaked across the whole process.** Auto-flush was
   a module-level flag flipped by the daemon, so once any daemon test ran,
   *every* tracker in the process began writing to the real `~/.aios` — found
   by noticing the test suite creating files in the user's home directory, not
   by a failing test. Made per-instance (`tracker.enable_persistence()`).
- **Ambient-state fragility fixed in earlier passes' tests:** giving the
  advisor a persisted fallback made `measured_prefix_reuse()` depend on the
  state dir, which broke Pass 192/193 tests that assumed "unmeasured". Those
  now isolate `AICTL_STATE_DIR` — they were silently environment-dependent.
- **Validation:** 29 new tests (`tests/test_new_features_195.py`), including a
  concurrency test asserting persisted totals equal in-process totals under 6
  threads, corruption resilience (truncated final line, garbage lines,
  unwritable log), and that routing still works when the log cannot be written.
- **Shutdown gap closed in Pass 196 (below).** Still open: the log is global
  rather than per-endpoint, so a multi-engine deployment gets one blended rate.

## Y. Drain reuse counters on shutdown, and a `clear()` cursor bug — ✅ implemented (Pass 196)

> **Status:** `aiosd.drain_reuse_counters()`, called from the shutdown handler.

- **The gap:** auto-flush fires only every `PREFIX_REUSE_FLUSH_EVERY` lookups,
  so up to that many lookups' worth of measurement was lost whenever the
  daemon exited. Immaterial to a rate over a long run — but it meant a daemon
  restarted more often than it flushed persisted *nothing at all*, making the
  case where the measurement matters least indistinguishable from the case
  where it matters most.
- **Deliberately a named module function**, not logic inline in the signal
  handler: shutdown paths are the least-exercised code in a daemon, and one
  buried in a closure could not be tested at all. It also cannot raise —
  an exception inside a signal handler would derail shutdown.
- **Bug found by the new tests:** `clear()` reset the hit/miss counters but
  **not** the flush cursors, so `lookups - flushed` went negative and a
  cleared tracker silently under-persisted — or persisted nothing — until it
  passed the stale cursor. Surfaced as a drain test recording 5 of 10
  lookups. Both cursors now reset with the counters they index into, with a
  direct regression test in Pass 195's file where the invariant belongs.
- **Ambient-state lesson repeated:** a first attempt asserted
  `persistence_enabled()` was False on the shared singleton, which only held
  in isolation — a daemon test elsewhere had opted it in. The tests now pin
  the state they assume and restore it, rather than inheriting whatever ran
  first. This is the third time in four passes that shared process state
  produced an order-dependent test; worth treating as a standing hazard in
  this codebase.
- **Validation:** 8 new tests (`tests/test_new_features_196.py`), including
  that sub-interval counts survive, that draining twice does not double-count,
  that an unwritable log returns rather than raises, and a guard asserting the
  shutdown handler still calls the drain (its removal would be silent data
  loss).

## Z. Self-audit of items T–Y — two defects in untested paths — ✅ fixed (Pass 197)

> `INSTRUCTIONS_OPUS.md` calls recently-added code the highest-yield audit
> surface. Re-reading items T–Y found two defects, both in paths the
> happy-path tests never took.

1. **The KV offload vendor gate fell open on an unnamed vendor.**
   `if vendor and vendor.lower() not in SUPPORTED_VENDORS` meant a
   *recognized-but-unsupported* vendor (`"huawei"`) correctly declined, while
   an *unidentified* one (`""`) skipped the check entirely and got a
   recommendation — the looser case was the one falling open. Emitting the
   flag for hardware the connector may not support produces a config the
   engine rejects at startup. Now an unnamed vendor is treated exactly like an
   unrecognized one. Cross-checked every vendor string the broker actually
   emits (nvidia/amd/intel/apple/huawei/qualcomm) against the gate.
2. **`engines conform` reported a false negative on chat.** When `/v1/models`
   does not answer and no `--model` is given, the probe invents
   `"test-model"`; engines that validate the name (vLLM) reject it, and the
   report read `chat completions: HTTP 404` — indistinguishable from a
   genuinely broken endpoint. In a module whose entire purpose is mapping
   probe results to honest consequences, conflating "your engine is broken"
   with "I guessed the model name wrong" is the worst defect it could carry.
   The detail now says which it is and points at `--model`; the caveat is
   deliberately *not* added when the user supplied a name or when
   `/v1/models` answered, since a failure there is a real signal.

- **Validation:** 8 new tests (`tests/test_new_features_197.py`), including a
  reproduction engine that validates model names and hides `/v1/models` —
  the exact combination that produced the false negative.

## M (remainder). Live fair-share admission — ✅ implemented (Pass 198)

> **Status:** `core/fair_scheduler.py` + an opt-in proxy gate
> (`fair_share_policy`, default `off`). Completes item M, whose advisory half
> shipped in Pass 190.

- **This is deliberately NOT VTC.** The VTC paper (arXiv:2401.00588, OSDI '24)
  motivates the work, but its exact virtual-counter update — the input/output
  weighting and the precise counter-lift rule — still could not be verified:
  arxiv.org (abstract, PDF *and* HTML) and every secondary source carrying the
  formula are egress-blocked from this environment, the same wall Pass 190
  hit. Rather than ship a guessed formula under the paper's name, this
  implements the two properties consistently reported across sources and
  textbook in their own right: **least-service-first** ordering, and a
  **new-arrival lift**. The output-token weight (2.0) is labelled in the
  source as an engineering default, explicitly *not* a number from the paper.
- **New-arrival lift matters for more than latency:** a client with no history
  starts at the current *minimum*, not zero. At zero, a fresh identity
  outranks every established one until it catches up — so anyone could reset
  their priority by rotating API keys.
- **Design flaw found by testing my own first version.** It compared each
  entity against the *even share*. With N entities that ratio is bounded above
  by N, so with two tenants a hog taking 98% of all tokens scored 1.96 and
  slipped under a threshold of 2.0 — **the gate was a no-op in the commonest
  multi-tenant case**. Now compares against the least-served entity, which is
  unbounded and expresses the actual intent. A starvation floor
  (`fair_share * 0.01`) stops a bucket sitting at zero from deferring every
  other tenant at once.
- **Fails open, everywhere.** Unknown entity, single tenant, no usage data,
  or any exception all admit. A fairness mechanism that denies service because
  it could not read usage data has traded a fairness problem for an
  availability problem, which is strictly worse.
- **Gate placement:** after `_model_trust_ok` and `_check_guard`, never
  before — an unsafe or untrusted request should be refused on those grounds
  regardless of whose quota it lands in. Returns **503**, not 403: being
  deferred is transient and retryable, not a permission failure. `warn` mode
  audits what enforcing *would* have deferred, so an operator can see the
  blast radius before turning it on.
- **Validation:** 29 new tests (`tests/test_new_features_198.py`), including
  the two-tenant regression, the starvation floor, key-rotation resistance,
  every fail-open path, config validation (a typo'd `"enfroce"` must be
  rejected rather than silently read as "off"), and source-level guards on
  gate ordering and the 503 status.
- **Still open:** admission is per-request and stateless — it consults
  cumulative usage, so a tenant deferred now stays deferred until others catch
  up, rather than being queued and released. Real queueing (and the rolling
  window from item M's original notes) would need scheduler state the proxy
  does not have today.

## AA. Indirect prompt injection via RAG — ✅ implemented (Pass 199)

> **Status:** `rag_screen_policy` (default `off`), `core/rag.screen_retrieved`.

- **The gap:** `core/guard.py` scans what a *user* types, via the proxy's
  `_check_guard`. `core/rag.py` had **zero** guard references — retrieved
  document text was joined into `context_blob` and handed to the model with no
  check at all. Indexed documents are a third-party data channel, so a
  document containing "ignore all previous instructions and …" reached the
  model verbatim and the prompt-side guard never saw it. The detection
  capability already existed; it was simply never applied here.
- **Grounding:** 2024–2026 work on indirect prompt injection converged on
  enforcing security *outside* the model with a deterministic check mediating
  what reaches it (CaMeL, FIDES, Progent, RTBAS, FORGE), rather than trusting
  the model to notice an instruction embedded in its context. Retrieval is
  where that boundary sits for RAG — the moment third-party text becomes
  prompt. Reported indirect-injection success rates of 50–84% mean "the model
  will probably ignore it" is not a defense.
- **Degrade, don't deny:** `enforce` drops the flagged chunk and answers from
  the rest. Failing the whole query would let one poisoned document deny
  answers the clean sources can still support. But if *every* retrieved source
  is quarantined, `answer()` refuses instead of replying from no context — an
  ungrounded answer presented as document-grounded is worse than an explicit
  refusal.
- **Fails open on scanner trouble:** a scan exception or an unimportable guard
  keeps the chunk. Screening is a filter on retrieval, not a gate on
  availability.
- **Validation:** 18 new tests (`tests/test_new_features_199.py`). The
  load-bearing one asserts the injected text never appears in the context
  handed to the model, with a companion test pinning that `off` still passes
  it through — so a regression in *either* direction is caught.
- **Both follow-ups closed in Pass 200 (below).** The "split across chunks"
  risk noted here turned out **not to exist** — see Pass 201.

## AB. Invisible-character bypass + ingest-time screening — ✅ implemented (Pass 200)

> **Status:** `core/guard.deobfuscate()` (detection-only normalization) and
> `index_directory(..., screen_policy=...)`.

1. **A bypass of Pass 199, found by testing it against the literature.** 2026
   RAG-poisoning work describes "human-imperceptible" payloads — instructions
   hidden in visually invisible text. Tested against our own guard: the plain
   payload was caught, but the same phrase with zero-width spaces interleaved
   (`Ignore​all​previous​instructions`) **sailed through every
   pattern** and reached the model. Invisible to anyone reviewing the
   document, fully tokenized by the model.
   - `deobfuscate()` maps concealment characters (zero-width space/joiner/
     non-joiner, word joiner, BOM, bidi embedding/override/isolates, soft
     hyphen) to **spaces** and scans that copy in addition to the original.
     Spaces, not deletion: an attacker interleaves those characters precisely
     to break a phrase, so deleting them yields
     `Ignoreallpreviousinstructions` and still matches nothing.
   - Fixed in `guard.scan` rather than in the RAG path, so the proxy's
     prompt-side gate gets the same protection — a user could obfuscate an
     injection just as easily as a document could.
   - **`processed` is never rewritten.** Detection uses the normalized copy;
     redaction still returns the caller's own text. Pinned by a test.
   - **No false positives on legitimate use:** ZWNJ is ordinary in Persian and
     Devanagari, so its presence alone is never a violation — only what it was
     concealing is. Tested with real Persian text.
2. **Ingest-time screening.** Security reviews of RAG rank ingestion-time
   filtering above generation-phase mitigations (one measuring embedding
   anomaly detection at ingest as outperforming three generation-phase layers
   combined). Pass 199 screened only at retrieval, so a poisoned document was
   re-scanned on every query and the operator only learned mid-answer.
   `enforce` now refuses to index it at all; `warn` indexes and reports. The
   `flagged` key is always present in the stats dict, so `--json` consumers
   get a stable shape — empty means "nothing flagged", never "not checked".
   A scanner failure mid-run leaves the store fully populated rather than
   half-indexed.
- **Validation:** 22 new tests (`tests/test_new_features_200.py`) covering
  each obfuscation family, the no-false-positive cases, retrieval-time
  screening of an obfuscated chunk, and all three ingest policies.

## AC. The "split across chunks" gap does not exist — ✅ corrected (Pass 201)

> Passes 199 and 200 both closed noting an unresolved risk: an injection split
> across two chunks, present in neither in full, evading chunk-level
> screening. Investigating it found **the claim was wrong**.

- **Why it cannot happen:** `chunk_text` overlaps consecutive chunks by
  `DEFAULT_OVERLAP` (200 chars) for retrieval-quality reasons that have
  nothing to do with security. The unwritten consequence: any phrase shorter
  than the overlap must appear *intact* in at least one chunk, because the
  overlap re-includes the previous chunk's tail at the next chunk's head. The
  widest blocking rule matches ~42 characters — a ~4.8x margin.
- **Confirmed empirically, not by argument:** an exhaustive sweep of 600
  consecutive byte offsets across a chunk boundary found **zero** evasions.
- **Why this still needed work:** the property is *accidental*. Nothing stops
  someone tuning `DEFAULT_OVERLAP` down for retrieval reasons, or adding a
  longer blocking pattern; either would silently delete a security property
  whose existence was never written down. Pass 201 adds an invariant test that
  fails loudly with an explanatory message if the margin ever closes —
  verified by temporarily setting the overlap to 20 and confirming the test
  fails with the right diagnosis.
- **Lesson recorded:** two consecutive passes propagated an unverified
  "still open" risk. Documenting a gap is not the same as confirming one, and
  a wrong entry in the backlog costs future work chasing a non-problem.
- **Validation:** 13 tests (`tests/test_new_features_201.py`) — the
  invariant, the byte-level sweep, start/end/short-document placements, a
  clean-document control proving the sweep is not passing vacuously, and a
  test pinning that consecutive chunks genuinely overlap.

### Correction to Pass 200's stated cause

Auditing Pass 200 found its own code comment mischaracterized the bug it
fixed. The comment implied the content rules had *no* Unicode normalization.
They did: `check_content` normalizes via `_normalize_with_map`, which
**deletes** zero-width characters — and deletion is exactly what made the
bypass work. Stripping them from `Ignore​all​previous​instructions`
leaves `Ignoreallpreviousinstructions`, one unbroken token no phrase pattern
can match. The problem was never missing normalization; it was normalization
that removed the separators instead of preserving the word boundaries they
stood in for. `deobfuscate()` maps them to spaces instead.

This matters because the wrong explanation invites the wrong fix: a maintainer
reading the old comment could delete `deobfuscate()` on the grounds that
`_normalize_with_map` "already handles zero-width" and silently reopen the
hole. Four tests now pin the distinction, including one asserting the two
normalizations *differ* and saying what to do if they ever converge.

Also confirmed during the audit: `detect_pii` was already Unicode-hardened
(zero-width stripped, homoglyphs folded, NFKC, with an index map so redaction
spans stay exact), and homoglyph substitution (`Ignоre` with Cyrillic о,
fullwidth Ｉ) was already caught. Only the zero-width-as-separator case was
open. Performance checked: de-obfuscating 1.5 MB costs 0.107 s and only runs
when concealment characters are actually present.

## Sources (Part 5 — Pass 199)

Indirect prompt injection: [Adaptive Evaluation of Out-of-Band Defenses
Against Prompt Injection in LLM Agents](https://arxiv.org/abs/2606.26479) —
notes the 2024–2026 convergence on deterministic out-of-model mediation
(CaMeL, FIDES, Progent, RTBAS, FORGE). Benchmarks and threat data:
[LivePI](https://arxiv.org/abs/2605.17986),
[AgentRedBench](https://arxiv.org/abs/2606.02240),
[ARGUS](https://arxiv.org/abs/2605.03378),
[AutoDojo](https://arxiv.org/abs/2606.15057), and the MDPI review of attack
vectors and defenses. Papers were reachable only as titles/abstracts via
search — arxiv.org itself is egress-blocked from this environment — so the
implementation uses the architectural principle they agree on, not any
specific paper's algorithm.

Pass 200 — RAG poisoning: [Towards Secure Retrieval-Augmented Generation: A
Comprehensive Review of Threats, Defenses and
Benchmarks](https://arxiv.org/abs/2603.21654) and [Securing RAG: A Taxonomy of
Attacks, Defenses, and Future Directions](https://arxiv.org/abs/2604.08304) —
source of both the ingestion-time-filtering priority and the
"human-imperceptible payload" vector that turned out to bypass Pass 199.
Also: [POISONCRAFT](https://arxiv.org/abs/2505.06579),
[Needle-in-RAG](https://arxiv.org/abs/2605.01782),
[Knowledge Base Poisoning for Policy-Aware LLM-RAG](https://arxiv.org/abs/2607.04379).
Perplexity-based and clustering defenses (TrustRAG, RobustRAG) were read about
but deliberately not implemented: both need a model or numeric stack this
project does not have, whereas Unicode concealment is deterministic and
stdlib-only. As before, papers were reachable only as titles/abstracts.

## AD. Speculative-decoding tuning advice — ✅ implemented (Pass 202)

> **Status:** `runtime/speculative.review_config()`, surfaced as a `warnings`
> key on `estimate_speedup()`.

- **The gap:** `generate_*_args` validated `num_speculative_tokens > 0` and
  nothing else, so a config of 20 draft tokens was accepted silently.
  Published tuning guidance puts the useful band at roughly **3-8**: below the
  floor throughput is left unclaimed, above the ceiling the expected accepted
  tokens per step plateaus, so the extra draft compute produces tokens that are
  almost never all accepted. Paying for drafts nobody accepts is the exact
  failure speculative decoding exists to avoid — and it is invisible, showing
  up as a disappointing speedup rather than an error.
- **Second silent failure surfaced:** an EAGLE3 draft head is trained on one
  target model's own generations. Pointed at a *fine-tune* of that model it
  drafts in the wrong style and acceptance falls, again with no signal. The
  advice says to verify against real traffic rather than assume the published
  rate. Deliberately not attached to `ngram` (drafts from the prompt itself,
  no training distribution to mismatch) or `mtp` (head ships with the target).
- **Advisory, not validation:** `generate_*_args` still only rejects values
  <= 0. A deployment with a measured reason to sit outside the band must not
  be blocked by a heuristic, and `review_config` never raises.
- **Checked and left alone:** `estimate_speedup`'s existing figures (1.1-2.1x)
  were compared against the same sources, which report 2-3x at realistic
  acceptance rates. Ours are conservative rather than over-promising, so no
  change — with a test pinning that they stay that way.
- **aictl's own defaults are clean:** auto-selected configs use 3 or 5 tokens,
  inside the band, so this adds no noise to the default path — pinned by a test
  so a future catalogue edit cannot drift out of it.
- **Follow-up caught immediately:** `spec methods` calls `estimate_speedup`
  but builds its JSON payload key-by-key and prints a fixed set of lines, so
  the new `warnings` key was dropped in *both* output modes — the same
  "built but unreachable" pattern item W caught for the KV offload advisor.
  Now wired into both, with tests asserting the caveat appears for an EAGLE3
  model and stays absent for the n-gram fallback (advice that fires on every
  model is noise, not advice).
- **Validation:** 22 tests (`tests/test_new_features_202.py`).

## Sources (Part 6 — Pass 202)

Speculative decoding tuning: [LK Losses: Direct Acceptance Rate Optimization
for Speculative Decoding](https://arxiv.org/abs/2602.23881),
[EAGLE-2](https://arxiv.org/abs/2406.16858), plus practitioner tuning guides
reporting the 3-8 band, EAGLE-3 acceptance of 60-80% in-distribution versus
40-60% for standalone draft models, and the fine-tune acceptance drop. As
throughout this session, arxiv.org is egress-blocked here, so papers were
reachable only as titles/abstracts; the implemented advice rests on the
quantitative guidance that appears consistently across the practitioner
sources, not on any single paper's algorithm.

## AE. FP8-first call-out when FP4 wins the quant ranking — ✅ implemented (Pass 203)

> **Status:** `cmd/quant._fp8_near_lossless_note()`, surfaced in both output
> modes of `aictl quant recommend`.

- **The gap:** deployment guidance for Blackwell is consistently "FP8 first,
  FP4 only if you need maximum throughput and can validate quality". aictl's
  scorer picks NVFP4 on a B200 — defensibly, 2.8x versus 1.3x for two quality
  points — but a user who cares more about fidelity than tokens/sec had to
  read the `quant compare` table to discover a 99% option was sitting right
  there, and got no signal that FP4 quality is workload-dependent enough to
  warrant validating.
- **Deliberately does not override the ranking.** The trade the scorer makes
  is reasonable; what was missing was disclosure. Mirrors the existing
  `_q4_k_m_sweet_spot_note` precedent exactly — surface the runner-up, leave
  the ranking alone — and a test asserts the note never tells the user FP4 is
  wrong.
- **Fires narrowly:** only when an FP4 format wins *and* FP8 actually fits.
  On H100 (AWQ wins) it stays silent; advice that fires everywhere stops
  being read.
- **Checked and left alone:** the underlying table already matches published
  comparisons — AWQ (96%) ranks above GPTQ (91%), the direction 2026 sources
  report for Llama 3+/Qwen 2+ class models, and FP8 (99%) above NVFP4 (97%).
  No data changes were needed, only disclosure. A test pins the AWQ > GPTQ
  ordering so a future catalogue edit cannot silently invert it.
- **Validation:** 15 new tests (`tests/test_new_features_203.py`).

## Sources (Part 7 — Pass 203)

Quantization: [Diagnosing FP4 inference: layer-wise and block-wise
sensitivity analysis of NVFP4 and MXFP4](https://arxiv.org/abs/2603.08747),
[Quantization-Aware Distillation for NVFP4 Accuracy
Recovery](https://arxiv.org/abs/2601.20088),
[FAAR: Format-Aware Adaptive Rounding for NVFP4](https://arxiv.org/abs/2603.22370),
[SOAR: Scale Optimization for NVFP4](https://arxiv.org/abs/2605.12245),
[Private LLM Inference on Consumer Blackwell GPUs](https://arxiv.org/abs/2601.09527),
plus 2026 practitioner benchmark round-ups reporting AWQ ahead of GPTQ by
0.5-1.0% perplexity at equal bit-width, Q4 at 1-3% perplexity loss versus
FP16, and the FP8-before-FP4 ordering for Blackwell. arxiv.org remains
egress-blocked here, so papers were reachable only as titles/abstracts; the
implemented change is a disclosure, not an algorithm from any single paper.

## AF. Plaintext HTTP to a remote engine — ✅ implemented (Pass 204)

> **Status:** a `transport` probe in `runtime/conformance.py`, plus a
> `--timeout` flag on `engines conform`.

- **How it was found:** comparing aictl against how the surrounding ecosystem
  (vLLM/SGLang/Ollama/LiteLLM) is actually deployed. Production-readiness
  checklists converge on the same short list — OpenAI-compatible requests
  **over HTTPS**, restart-on-failure, per-key rate limiting, metrics with real
  alerts, benchmarked performance. aictl already covered the last four
  (quotas/apikeys, quadlet units, Prometheus rules, `bench`); the first was
  unchecked.
- **The gap:** `engines conform` accepted `http://10.0.0.5:8000` silently. The
  Authorization header and every prompt and completion cross the network in
  cleartext, and nothing said so.
- **A deliberate fourth severity.** `INSECURE` is not `degraded` (nothing
  about output quality changes, so that would misdescribe it) and not
  `required` (the engine works, and calling a working engine broken would be
  wrong) — but it *does* count against `conformant`, because a deployment
  shipping API keys in cleartext is not production-conformant whatever its
  response quality. Tests pin that it is counted as none of the other three.
- **Loopback is exempt.** aictl's own defaults are `127.0.0.1`, traffic there
  never reaches a wire, and flagging it would make the check fire on every
  local deployment. Advice that always fires stops being read.
- **Runs without the network,** since it is a property of the URL rather than
  of the server — so an unreachable endpoint still gets the finding.
- **Wording fixed on the way:** the impact line said "transport unavailable",
  which is wrong — the transport is present, it is just exposing traffic. The
  label is now severity-aware.
- **`--timeout` added:** the new tests ran for 55s against unroutable
  addresses, which surfaced that `conform` had no way to lower the 5s
  per-probe default. Useful in its own right (quick local check, slow remote
  one) and it brought those tests to 1.4s.
- **Validation:** 21 new tests (`tests/test_new_features_204.py`); the two
  probe-shape assertions from item T were updated, since the report genuinely
  gained a probe.

## Sources (Part 8 — Pass 204)

Ecosystem comparison rather than papers this time: 2026 production
comparisons of vLLM / SGLang / Ollama / LM Studio / TensorRT-LLM and
OpenAI-compatible API guides covering LiteLLM. The recurring single-node
readiness checklist ("HTTPS, restart on failure, per-key rate limiting,
metrics with three real alerts, benchmarked performance") is what this pass
audited aictl against.

## AG. Schema design review for constrained decoding — ✅ implemented (Pass 205)

> **Status:** `aictl guided lint <schema>` (`runtime/schema_lint.py`).

- **The gap:** `guided validate` answered "does this document match this
  schema?". Nothing answered the prior question — "is this schema one a model
  can fill in well?" Constrained decoding gives a **format guarantee, not a
  semantic one**: a schema can be perfectly valid, compile fine in XGrammar,
  and produce parseable output on every request while making the answers
  worse. That failure is invisible precisely because the structural check
  passes.
- **The load-bearing check is field ordering.** Generation is autoregressive,
  so a schema emitting `answer` before `reasoning` forces the model to commit
  to a conclusion and then rationalize it — it cannot think first if the
  grammar will not let it write first. Both `properties` order and `required`
  order are checked, since backends differ in which they emit by, and a schema
  can be correct in one and wrong in the other.
- **Also checked:** nesting depth (4+), field count (50+), undescribed fields,
  and optional fields that cannot be null (the model can neither omit the
  field nor say it has no value, so it invents one).
- **Conservative by construction.** The reasoning/answer keyword sets are
  short and unambiguous: a false positive tells someone their schema is wrong
  when it is not, which is worse than staying quiet. Tests cover
  answer-only, reasoning-only, and unrelated-field schemas staying clean.
- **Advisory, never blocking.** Good schemas break these rules deliberately,
  so `lint_schema` never raises, `guided lint` exits 0 by default, and
  `--strict` fails only on warning-level findings — info-level observations
  never fail a build.
- **Validation:** 30 new tests (`tests/test_new_features_205.py`), including
  malformed-schema robustness and array traversal (a deep structure hidden
  behind `items` must still be seen).

## Sources (Part 9 — Pass 205)

Structured output: [JSONSchemaBench](https://arxiv.org/abs/2501.10868)
(rigorous benchmark of structured-output engines; found Outlines' compliance
limited largely by schema-compilation timeouts),
[XGrammar](https://arxiv.org/abs/2411.15100) and
[XGrammar-2](https://arxiv.org/abs/2601.04426) (now the default structured
backend for vLLM/SGLang/TensorRT-LLM),
[SLOT](https://arxiv.org/abs/2505.04016). The schema-design pitfalls
implemented here — reasoning-after-answer ordering, deep nesting, large
schemas, missing descriptions, absent null handling — come from practitioner
guidance on grammar-constrained generation, which consistently stresses that
the guarantee is structural rather than semantic. arxiv.org is egress-blocked
here, so papers were reachable as titles/abstracts only.

## AH. Musk algorithm, steps 2 and 4 — ✅ implemented (Passes 207-208)

This session had been running steps 3 and 5 (optimize, automate) while
skipping 1 and 2 (question, delete) — exactly the failure the algorithm names:
optimizing something before asking whether it should exist.

### Step 2 — Delete (-333 lines, zero behaviour change)

26 functions and classes referenced nowhere in `aictl/`, `tests/`, or `docs/`,
including 7 of `stack/systemctl.py`'s 10 functions. **The unchanged 3734-test
suite is the evidence they were dead** — nothing noticed them leave.

Deliberately kept, having checked: `format_for_user` and the `AictlError`
base (`__main__` calls the former on every bubbling exception, and its first
branch is an isinstance check against the latter), and the 9 unreferenced
constants, which document *external* defaults — lmdeploy's 23333, LM Studio's
1234, the bootc image. Delete applies to code that does nothing, not to values
that carry information.

### Step 4 — Accelerate cycle time (gate 59s → 30s)

**Measured before optimizing.** Of `aictl gate`'s ~59s, the suite is ~57s and
every other phase combined is 2.8s. "Make the gate faster" therefore meant
exactly "make the suite faster"; work on anything else would have gone into
the 5%.

`core/partest.py` runs each test *file* in its own process. Files are already
independent, while order *within* a file is preserved — which matters, since
unittest orders methods alphabetically and some tests rely on it.

Isolation arrived with the speed rather than after it: each worker gets its
own state directory, because otherwise workers race on `~/.aios` — and the
suite would keep writing to the user's real state directory, a bug already
found in this codebase. One change bought both.

**Serial remains the source of truth.** `--parallel` is opt-in, because a
parallel run is only ever evidence *about* the serial result.

### The defect parallelization exposed

Running files in isolation revealed that `test_e2e_stories`'s cost-tracking
test asserted "the first ask is a cache miss" **without ensuring the cache was
empty**. It passed only because discover-order happened to leave the right
state, and failed standalone — bisected to a specific preceding test. Fixed by
establishing the precondition instead of assuming it. This is the fifth
shared-mutable-state defect found this session; the class is now the single
most reliable source of bugs in this codebase.

### Step 3 — Simplify (-101 lines from 4 files, +9)

The same ~14-line state-isolation setUp/tearDown had been copy-pasted into ten
places across four test files. Duplicating a *tricky* pattern is worse than
duplicating a simple one: ten copies are ten chances to forget the
`AIOS_STATE_DIR` half, or to restore the environment in a way that leaks when a
test fails. Getting it wrong caused two defects here — the suite writing into
the developer's real `~/.aios`, and tests passing only in discover order.
`tests/support.py` now holds one implementation (`IsolatedStateTestCase`,
`IsolatedTrackerTestCase`), using `addCleanup` so restoration survives a
failing setUp, which a plain tearDown would not.

### Step 5 — Automate (last, and only now)

Automating a process you do not understand makes the wrong thing happen
faster. This one earned it: the same three CLAUDE.md numbers were hand-edited
about a dozen times in one session, always with the same `sed`, always after
the same trigger. Nothing checked them, so a miscount silently shipped a false
claim about the project's size — and those numbers are the first thing any
reader sees.

`core/docsync.py` + a `Counts` phase in `gate`. Deliberately split: check is
pure and cheap so the gate runs it every time; sync writes. A verification step
that silently rewrote files would be worse than the sed it replaced. Verified
by breaking it on purpose — a wrong count fails the gate with the exact
numbers.

The test *count* is not recomputed (that would cost 57s to check a comment);
the gate passes in the number it just measured. Count what is cheap, accept the
expensive number from whoever already paid for it.

Same pass: gate's CHANGELOG check hardcoded `"v1.7.0"`, a literal needing a
hand-edit at every bump — a forgotten edit would leave it passing against the
previous release forever. Now derived from `VERSION`, closing weakness #2/#8
from `REVIEW_v1.7.0.md`.

### What step 1 actually found: the SDK fabricates answers

Questioning the error taxonomy — 8 of 9 exception classes never raised —
looked like a deletion candidate. But the requirement they serve ("an error
says what happened and what to do") is good and has a name attached, so the
real question became *where should they fire and don't*.

The answer was worse than an unused class. With no engine running,
`aictl.ai.ask()` did not fail — it started an in-process mock whose docstring
said the substitution was "invisible to the developer", and returned plausible
text attributed to a **real hardware-derived model name** (`llama3.2:1b`) with
a **non-zero cost for inference that never happened**. A user could `import
aictl`, get confident answers, ship it, and never learn the output was
fabricated; `tco`/`cost` would report spend that did not occur.

This is the same defect class the rest of the session kept fixing —
undisclosed degradation — sitting at the primary library entry point.

Fixed by disclosure, not removal: zero-config still works (a real feature),
but the response carries `mock=True`, names the model `"mock"`, reports zero
cost, and says `MOCK` in its repr. `ai.status` gained a `mock` key, since
"what am I actually running against?" is the question it exists to answer.

Two further defects fell out:

* An existing test asserted `cost_usd > 0` while running against the mock — it
  **encoded** the fabrication rather than catching it.
* `model` returned `"mock"` for *"no model configured"*, conflating that with
  *"a mock produced this"*. Now `"unknown"`, because those are different
  states and naming them alike was the ambiguity being fixed.

A first version of the new test asserted the *biconditional* (named "mock" iff
mock-served) and failed: `AICTL_MODEL` lets a user point a real engine at a
model they have named "mock". The test was wrong, not the code — the
requirement is one-directional, and it now says so.

### Counts: sync, do not merely complain

The `Counts` gate phase initially *failed* on a stale number, which broke two
unrelated tests asserting the gate returns 0 and — worse — left the human
running the sed anyway. That turns the gate's verdict into a statement about
documentation rather than code. Derived data should be derived: it now syncs
and reports what it changed, so the manual step disappears rather than
becoming a reminder. The rewrite is never silent.

### Steps 1, 3, 5 — status

Step 1 (question requirements) produced the measurement in
`docs/REVIEW_v1.7.0.md`. Steps 3 and 5 have been this session's default mode
and are well covered. The **remaining step-1/2 finding is deliberately not
acted on unilaterally**: 8 of 9 exception classes are never raised, so
`format_for_user`'s `AictlError` branch never fires in practice, and six
survive only because tests reference types no code path produces. Cutting
them — or the observability command overlap — changes documented v1.6.0
release surface, which is a maintainer's decision, not an agent's.

## AI. The Go port does not build — ⚠️ reported, deliberately not "fixed" (Pass 211)

> **Status:** surfaced by a new `Go port` phase in `gate`
> (`core/goport.py`). **`go.sum` was left untouched, on purpose.**
>
> **Superseded in part by AJ.** The diagnosis below — "a corrupted or
> hand-edited entry" — was too charitable by an order of magnitude. Looking
> harder showed four fabricated entries in a file the Go toolchain never wrote.
> The refusal to write in proxy-derived values survived; the entries were
> **removed** instead, which restores checksum-database verification rather
> than retiring it.

- **Found by asking what `gate` does not check.** It is this project's "is
  everything all right?" command and it verified only the Python half — the
  2,176-line Go port that CLAUDE.md and RELEASE.md both advertise as "29 Go
  commands" had no automated check at all.
- **It is worse than untested: it does not build.** `go.sum` records a
  checksum for `github.com/spf13/cobra v1.8.1` that disagrees with what the
  module proxy serves, and Go correctly refuses with a SECURITY ERROR.
- **The hashes point at corruption, not compromise:**

      downloaded: h1:e5/vxKd/rZsfSJMUX1agtjeTDf+qv1/JdBF8gg5k9ZM=
      go.sum:     h1:e5/vxKd/rZsfSJMUX1agtjeTDf+qv1/JdBF8lex5Gm=

  They share a 38-character prefix and differ only in the tail. Independent
  hashes differ everywhere, so this reads as a corrupted or hand-edited entry
  rather than a substituted artifact.
- **Why `go.sum` was not repaired.** Rewriting it with whatever the proxy
  happened to serve *is* the control `go.sum` exists to provide, and the
  authoritative value could not be checked — `sum.golang.org` is egress-blocked
  from this environment. Reporting an unverifiable state honestly is correct;
  silently "fixing" a checksum is the one thing that must not happen. Tests pin
  that no code writes `go.sum` and that `go mod tidy` is never invoked, since
  it would rewrite the file as a side effect.
- **What was fixed is the silence.** A gap in the gate's output can be acted
  on; one that never appears there looks like health. The phase reports without
  failing: a missing toolchain or unreachable proxy is a property of the
  machine, the same reasoning the security phase already applies to host
  findings, and each failure mode is classified separately because the remedies
  differ entirely.
- **For the maintainer:** run `cd go-port && go mod tidy` on a machine with
  access to `sum.golang.org`, then verify the resulting `go.sum` against the
  public checksum database before committing.
- **Validation:** 16 new tests (`tests/test_new_features_211.py`).

## AJ. The `go.sum` was fabricated — ✅ removed, not replaced (Pass 212)

> **Status:** four fabricated entries deleted; `lint_go_sum()` added to
> **`core/goport.py` and run by `gate` on every invocation.
>
> **Completed by AL.** The checksum database turned out to be reachable by a
> different egress path, so every entry has since been verified and restored,
> and the Go port now builds. The reasoning below still stands as the correct
> move while the database was believed unreachable.**

- **Found by questioning the previous pass's own conclusion.** AI reported the
  checksum as unverifiable and stopped. Two questions had gone unasked: whether
  the recorded value was even *shaped* like a hash, and how far the damage went.
- **It was not a hash.** The cobra `h1:` value was 43 base64 characters, which
  cannot decode to a 32-byte SHA-256. That is decidable locally, in
  microseconds, with no network and nobody's authority — and it means there was
  never a competing attestation to weigh against the proxy's.
- **Four of eight entries were wrong, and the shape is the finding:**

      mousetrap    wN+x4NVGpMsO7ErU QYnwIlCDoM6PDIBo7tSrmkPvXss=   claimed
                   wN+x4NVGpMsO7ErU n/mUI3vEoE6Jt13X2s0bqwp9tc8=   real
      blackfriday  +Rmxgy9KzJVeS9/2gXHxylqXiyQDYRxCVz55j GbOGsM=   claimed
                   +Rmxgy9KzJVeS9/2gXHxylqXiyQDYRxCVz55j meOWTM=   real
      cobra (zip)  e5/vxKd/rZsfSJMUX1agtjeTDf+qv1/JdBF8 lex5Gm=    claimed
                   e5/vxKd/rZsfSJMUX1agtjeTDf+qv1/JdBF8 gg5k9ZM=   real

  Long shared prefix, then a plausible-looking tail. Independent SHA-256 values
  share essentially no prefix, so 36 characters of agreement is neither chance
  nor corruption in transit — it is a value reproduced from memory and finished
  by guesswork. The file also omitted entries a real `go mod tidy` emits
  (`gopkg.in/check.v1`, `gopkg.in/yaml.v3`). It was a thing shaped like an
  attestation, attesting to nothing, in a repository whose own `aictl trust`
  subsystem exists to verify artifacts.
- **Removed rather than replaced — the direction is the entire point.** Once a
  hash sits in `go.sum`, Go trusts it and never consults the checksum database
  again. Writing in proxy-derived values would have turned the gate green by
  converting *unverified once, in a sandbox* into *unverified permanently, for
  everyone*. With the entries absent, Go **must** ask `sum.golang.org` and
  records a verified hash on the first `go mod download` from any machine that
  can reach it. Removal requires trusting nothing; that is what makes it safe,
  and it is why the tempting repair was the worse one.
- **Why the verified values were not simply generated here.** `sum.golang.org`
  is not in this environment's network allowlist (`proxy.golang.org` is), and
  no signed checksum-database tiles were cached locally. Both were checked
  rather than assumed. `GOSUMDB=off` was used exactly once, in a throwaway
  copy, purely to measure how many entries were wrong — nothing from it was
  shipped, and a test now asserts no shipped module mentions `GOSUMDB`,
  `GONOSUMDB`, `GOPRIVATE` or `GOFLAGS`.
- **The generalisable half.** `lint_go_sum()` checks every entry's
  well-formedness on each `gate` run: 0.04s, no network, no toolchain, no
  build. It cannot say an entry is *correct* — only the checksum database can —
  but a value that no hash function could have produced can never sit in this
  tree unnoticed again. `gate` also now distinguishes *not yet recorded* from
  *broken*, so absent checksums no longer read as a Go defect.
- **For the maintainer:** `cd go-port && go mod download` on a machine with
  normal network access writes the verified `go.sum`; it is safe to commit.
  See `go-port/README.md`.
- **Validation:** 26 new tests (`tests/test_new_features_212.py`).

## AK. The state directory was decided in 34 places, so it split — ✅ fixed (Pass 213)

> **Status:** one `resolve_state_dir()` in `core/state.py`; 34 ad-hoc
> resolutions across 28 files removed; `--state-dir` now reaches every writer.

- **Found by reading the one line of output nobody reads.** Walking the
  first-run journey with the state directory redirected, `aictl init` printed
  `State dir /root/.aios` while `AIOS_STATE_DIR` pointed somewhere else.
- **Two writers, two directories, no warning:**

      $ AIOS_STATE_DIR=/tmp/s aictl init && aictl chat hi
      /tmp/s/perf.jsonl        <- twelve modules honoured the variable
      ~/.aios/state.json       <- StateStore did not

  `DEFAULT_STATE_DIR = Path.home() / ".aios"` was a module constant evaluated
  at import, so `StateStore` — owner of `state.json`, `models.db`, the audit
  log and the API keys — consulted no environment at all.
- **A printed remedy was false.** `core/errors.py` answers a `PermissionError`
  with "run with `AIOS_STATE_DIR=/tmp/aios`". That advice did not move the file
  whose permissions were the problem.
- **Three layers, each found by fixing the one above it:**
  1. twelve modules read `AIOS_STATE_DIR`, two read `AICTL_STATE_DIR`, and
     `StateStore` read neither;
  2. `--state-dir` had the same shape of bug one level down — it moved
     `state.json` and left `perf.jsonl` behind, because a dozen helpers resolve
     the directory with no argparse namespace in hand. A global flag only moved
     what was handed it explicitly;
  3. **fifteen further modules imported `DEFAULT_STATE_DIR` directly** —
     `config.json`, the API keys, the audit log, the metering ledger, tenants,
     plugins. The most sensitive files in the product ignored both names.
  Layers 2 and 3 were caught by tests written for layer 1, not by inspection.
- **Fixes.** `resolve_state_dir(explicit=None)` decides once: explicit argument,
  then `AIOS_STATE_DIR`, then the `AICTL_STATE_DIR` alias, then `~/.aios`. The
  argument wins because the flag is the user being specific right now. Empty
  values mean unset rather than the current directory, which would scatter
  state through whatever tree the user was standing in. `__main__` publishes
  `--state-dir` into the environment so it reaches every helper and every
  subprocess. `PLUGIN_DIRS` became `plugin_dirs()` — a module-level list froze
  the directory at import, before the flag had even been parsed.
- **The regression guard matters as much as the fix.** This bug existed because
  one rule was copied 34 times, so a test walks the AST of every module and
  fails on a 35th copy. It parses rather than greps: modules legitimately name
  the old constant in prose when explaining this history, and a substring check
  fails on documentation instead of on a real reference — the same mistake made
  twice before in this session.
- **The suite was not hermetic either.** Sweeping every test file against a
  redirected `HOME` showed **53 of 280** left `models.db`, `rag.db`,
  `sem_cache.db`, `perf.jsonl`, the audit log or the daemon logs in the real
  `~/.aios`. Running the tests mutated real data — and worse, a test could pass
  on what a previous run had left there, which is precisely the order-dependent
  failure this codebase has already hit twice. Repairing 53 files individually
  would have been optimising something that should not exist: every one of
  those artifacts is a state-directory artifact, so `tests/__init__.py` now
  redirects the state directory once, before any test module imports `aictl`.
  It honours an already-set value so `core/partest.py`'s per-worker
  directories still work.
- **Validation:** 26 new tests (`tests/test_new_features_213.py`), five of them
  running `aictl` as a subprocess against a redirected `HOME`, because the
  property is about process-wide resolution and an in-process test shares
  already-imported modules. Suite 3878/3878, and `~/.aios` stays untouched.

## AL. The Go port builds — ✅ checksums verified, compile error fixed (Pass 214)

> **Status:** all ten `go.sum` entries read from `sum.golang.org` and pinned in
> a test; `internal/runtime/broker.go` fixed; `go build`, `go vet` and
> `go test ./...` all clean. `gate` now reports `Go port · go build ./...
> succeeded`.

- **Found by questioning "blocked".** Two passes had reported the checksum
  database as unreachable, which was true of the HTTPS proxy (`sum.golang.org`
  is not in this environment's allowlist; `proxy.golang.org` is) and of the
  `/sumdb/` proxy endpoint and the local tile cache — all three were checked.
  What had never been tried was a *different egress path*. `WebFetch` reaches
  it. The constraint was real but narrower than the conclusion drawn from it.
- **All ten entries verified.** Each was read from the checksum database and
  compared against what the module proxy served; every one agreed, including
  the four that the fabricated file had got wrong. The values are pinned in
  `tests/test_new_features_212.py`, so an edit that changes a hash fails until
  whoever makes it re-verifies. A second test asserts no *unpinned* entry can
  ride along, which is the direction that would otherwise go unnoticed.
- **The barrier was hiding a real defect.** With the checksums fixed, the build
  immediately failed on an unused `path/filepath` import in
  `internal/runtime/broker.go`. The `SECURITY ERROR` had masked an ordinary
  compile error for however long both had existed.
- **A documented claim was checked and left alone.** `--help` lists 31
  commands; two of those (`help`, `completion`) are Cobra built-ins, so the
  "29 Go commands" in CLAUDE.md and RELEASE.md is exactly right. This is the
  third figure this session that looked wrong and was not — counting is not
  measuring, in both directions.
- **Two tests were rewritten rather than deleted.** `test_the_fabricated_file_
  is_gone` asserted the file's absence and asked, in its own failure message,
  "was it generated by `go mod download`, or written by hand?" That was the
  right question; the assertion now answers it by pinning the checksum-database
  record instead of merely permitting the file back.
- **Validation:** suite 3882/3882 (44 in passes 211–212 rerun green), `go vet`
  clean, `go test ./...` passing across three packages, gate GREEN.

## AM. The command surface was hand-copied four times — ✅ derived (Pass 216)

> **Status:** `core/cli_surface.py` is the single derived source; `gate`'s Docs
> phase, `help advanced` and all three shell completions now consult it.

- **Found by finishing an earlier pass's unfinished business.** Pass 213 noticed
  that `gate`'s Docs phase built the full parser, computed
  `set(a.choices.keys())` — the true 80-command surface — and **threw it away
  unassigned**, then checked a 10-name list frozen at v1.6.0. Three lines below
  it, the CHANGELOG check was already *derived* from `VERSION`, with a comment
  explaining exactly why hardcoded literals rot. The argument had been applied
  to one check and not the two beside it.
- **Three more copies of the same fact, all drifted:**
  - `help.py` told users this was "the full 65-command surface" — it is 80 — and
    hand-maintained a category listing that the gate's own check could never
    verify, because the names were written without the `aictl ` prefix it greps.
  - `completion.py` hardcoded **three** lists: bash 38 names, zsh 17, fish 38.
    Up to 63 of 80 commands had no tab completion, and the bash subcommand table
    knew five of `model`'s eight subcommands. This failure mode is invisible by
    construction: you cannot tab-complete a command you do not know exists, and
    you never learn the completion script was at fault.
- **One derived source, consulted at call time** so plugin-registered commands
  ride along. `build_parser` is imported lazily inside each function — the
  pattern `gate.py` and `help.py` already used — so `aictl.core` importing the
  CLI surface cannot cycle with `aictl.__main__`.
- **The Docs phase also gained the reverse direction:** documentation naming a
  command that does not exist. The matching rule is *structural*, not a stopword
  list — an earlier probe's `\s` crossed newlines and turned prose into ghost
  commands, and markdown legitimately says "aictl does" — so only fenced or
  backticked contexts count. Two checks guard the checkers: a README scan that
  finds zero references fails (a matcher matching nothing is vacuously green
  forever), and a test patches the derivation to `{}` and asserts the Docs
  verdict *changes* — the one thing the discarded-set version could not do.
- **The floor is honest about being a floor.** `DOCS_MIN_TOPIC_COMMANDS = 25`
  replaces the frozen critical list; the 8 curated topics cover 33 and are
  guides, not a per-command reference, so the check catches the help collapsing
  rather than pretending every command needs a guide.
- **Two tests corrected rather than deleted.** Pass 207's CHANGELOG test read
  `gate.run`'s source for `expected_release`, which moved into `_docs_issues`;
  it now reads the helper *and* asserts the behaviour with a stale CHANGELOG.
  A new test of mine grepped `help.py` for "65" and failed on the comment
  explaining this history — the prose-versus-behaviour mistake made three times
  this session, now checking what users actually see.
- **Validation:** 24 new tests (`tests/test_new_features_216.py`); suite
  3929/3929; gate GREEN twice serial and once parallel; `bash -n` parses the
  generated completion; all three shells verified to cover 80/80.

## AN. A threshold that lied, and two counts nobody checked — ✅ derived (Pass 217)

> **Status:** the MCP phase now asserts declared/dispatched parity instead of
> `>= 16`; `docsync` verifies the Python, Go and MCP surface counts the docs
> advertise.

- **Found by finishing pass 216's own leftovers.** That pass derived the CLI
  command surface and left two siblings in place, both the same disease.
- **`len(TOOLS) >= 16` against 19 tools.** Three could be deleted and the gate
  would still pass, printing "16 tools registered" as a success line. Worse, a
  count cannot see the failure that matters in *either* direction: a tool
  **declared but not dispatched** is advertised in `tools/list` and then errors
  when a client calls it — undisclosed degradation, this session's recurring
  theme — and one **dispatched but not declared** is unreachable code. The
  pairing is the invariant; the number never was. Both sets are 19 today, so
  this fixes the check rather than a live defect.
- **Reachability is read from the AST, deliberately.** Every handler does real
  work — hardware detection, a security scan, an LLM call — so probing by
  invocation would make the gate slow and side-effecting. Which names
  `_dispatch_tool` compares against is a static property, so it is read
  statically, and *parsed* rather than grepped: every tool name also appears in
  the module's comments, so a grep-based reader would agree by accident rather
  than by fact. The fourth time this session that parsing beat grepping.
- **"80 Python + 29 Go commands" and "19 MCP tools" were never verified.**
  `check_counts` compared test files and test counts and stopped, so the first
  claims any reader meets were hand-maintained and right only by luck. Now
  derived: Python from the parser, MCP from the declared tools, Go from the
  single `root.AddCommand(...)` block.
- **The Go count is read from source, avoiding a trap.** Cobra adds its own
  `help` and `completion`, so the built binary lists 31 while the port defines
  29. The documentation claims the registrations — 29 is correct, and a check
  built on `--help` would have "fixed" a number that was already right. Third
  time this session a figure looked wrong and was not.
- **Each count degrades independently to None and is skipped**, never compared
  against zero, which would report every document as wrong. The same rule
  `test_count=0` already followed.
- **The checkers are themselves checked**: a test asserts the declared set is
  non-empty (two empty sets are equal, which would make the parity check
  vacuously true forever), and each new count check is proven to *catch* a
  wrong number rather than merely returning clean on a correct repo.
- **Validation:** 19 new tests (`tests/test_new_features_217.py`); suite
  3948/3948; gate GREEN twice serial and once parallel.

## AO. The last unverified count, and the registration step people forget — ✅ (Pass 218)

> **Status:** every documented surface count is now derived; `gate` catches a
> command module that was never registered; three command counters became one.

- **"30 REST API endpoints" was the last documented number nothing checked.**
  Passes 216–217 derived the CLI commands, Go commands and MCP tools; this
  finishes the set. Routes are literal dicts in the daemon's `do_GET`/`do_POST`
  handlers, so they are read from the AST rather than by starting a daemon and
  probing it — slow and side-effecting for a number that checks one line of
  documentation.
- **The exclusion is the interesting part.** The handler has **31** routes;
  `/metrics` serves Prometheus text exposition rather than JSON, so the REST
  API the docs count is the 30 under `/v1/`. A naive route count earlier in
  this session concluded the documentation was wrong by one. It was not — the
  fourth documented figure this session that looked wrong and was right.
  Counting is not measuring, in both directions.
- **A command module can exist without being registered.** CLAUDE.md's own
  workflow says "Register new commands in `__main__.py`" — exactly the kind of
  step a person forgets. Such a module imports cleanly, may carry its own
  tests, and the command simply does not exist for any user. Nothing compared
  the directory against the parser; now `gate` does.
- **Two modules are deliberately named `<command>_cmd.py`** because their
  command name is a Python keyword or builtin (`import`, `cache`), so the
  suffix is stripped before the lookup. A check that flagged those two would
  have been turned off within a day.
- **Three places counted the same thing.** `info._count_commands()` walked the
  parser itself and returned on the *first* subparsers action, so a second
  would have gone uncounted; it now delegates to the one derived source.
  `docsync.count_commands()` is retained deliberately — it counts modules on
  disk, which is a *different* measurement from what the parser registers, and
  the gap between them is precisely the forgotten-registration check above.
- **Each new check is proven to catch a fault**, not merely to return clean on
  a healthy repo: a synthetic handler with two `/v1/` routes and a `/metrics`,
  a synthetic `aictl/cmd/` holding an unregistered module, and a CLAUDE.md
  claiming 7 REST endpoints.
- **Validation:** 16 new tests (`tests/test_new_features_218.py`); suite
  3964/3964; gate GREEN twice serial and once parallel.

## AP. Two pinned constants named images that do not exist — ✅ (Pass 219)

> **Status:** one `RUNTIME_IMAGES` map in `constants.py`; `:latest` removed
> from every engine path; both wrong images corrected against the registry.

- **Found by following CLAUDE.md's own rule** — "All constants in
  `constants.py` — no hardcoded ports/versions" — into the deployment paths,
  where three modules hardcoded engine images instead.
- **The same product emitted different vLLM versions per path.** `VLLM_IMAGE`
  (`v0.19.0`) was used by disagg, modelservice, kserve and deploy, while
  quadlet and orchestrator shipped `vllm/vllm-openai:latest`. A user comparing
  a local `aictl apply` against `aictl deploy modelservice` was running two
  different builds, and the local one changed under them silently.
- **Then the constants themselves were wrong, and which ones is the finding:**

      VLLM_IMAGE    vllm/vllm-openai:v0.19.0   used by 4 modules   exists
      SGLANG_IMAGE  lmsys/sglang:v0.5.9        used by nobody      404
      OLLAMA_IMAGE  ollama/ollama:0.20         used by nobody      404

  The two nobody used were never exercised, so nothing discovered they were
  unpullable. `lmsys/sglang` does not exist at all — the SGLang project
  publishes to `lmsysorg` (11.6M pulls) — and `ollama/ollama:0.20` does not
  exist because the tag scheme is MAJOR.MINOR.PATCH, so the intended v0.20 is
  `0.20.0`. Same signature as the fabricated `go.sum`: values that were never
  exercised were never verified, and they were wrong.
- **Verification changed the fix.** "Single-source the deployment paths through
  `SGLANG_IMAGE`" is the obvious tidy-up and would have pointed three *working*
  paths at a repository that cannot be pulled. Every value was checked against
  the registry before being wired in. The tidy version of this change was the
  broken one.
- **`:latest` is gone from every engine path.** A floating tag in a generated
  Quadlet unit or KServe CRD cannot be pinned by digest, changes under the
  operator without warning, and cannot be verified by the `aictl trust`
  subsystem this product ships. `trt-llm` stays floating **deliberately**: it
  is on NGC rather than Docker Hub, so its tag could not be checked the same
  way, and pinning it to a guess would be the same error inverted — the
  reasoning that governed `go.sum`.
- **Left alone, and stated:** the `:latest` tags on ComfyUI, Tabby and Whisper
  in `manifest.py` are third-party *application* recipes rather than inference
  engines, and pinning them would need three more upstreams verified.
- **Validation:** 14 new tests (`tests/test_new_features_219.py`), including a
  guard that no deployment module hardcodes an engine image again; suite
  3978/3978; gate GREEN twice serial and once parallel.

## AQ. Three models the filter existed to find could not be found — ✅ (Pass 220)

> **Status:** `--use-case` choices and the MCP `use_case` enum are both derived
> from the catalog via `catalog_use_cases()`.

- **Found by probing the model catalog for unverified external facts** — the
  same vein that produced the two container images naming nothing. The
  external half could not be checked (see below), but the *internal* half was
  checkable and wrong.
- **The catalog holds six use cases; the flag offered five:**

      catalog: chat(24) code(4) embedding(3) vision(2) stt(1) reasoning(3)
      flag:    chat     code    embedding    vision    stt

  So `aictl recommend --use-case reasoning` was an argparse error, and
  `qwen3:7b-thinking`, `qwen3:32b-thinking` and `phi4-reasoning:14b` — the
  three best local reasoning models in the catalog — were unreachable through
  the filter that exists to reach them. The MCP tool schema had the same gap,
  so a client was told `reasoning` was not a valid value.
- **Both directions are now checked.** A catalogued case the flag cannot offer
  hides models; a flag choice with no models behind it returns an empty list
  and reads to the user as a hardware problem rather than a bad filter.
- **What this pass deliberately did not do.** The catalog also asserts 31
  Ollama model names, and several look questionable beside a registry that
  hosts text LLMs — `whisper:large-v3` under runtime `ollama` most obviously.
  Both `ollama.com` and `registry.ollama.ai` are outside this environment's
  egress allowlist, verified by two independent paths, so those names could
  **not** be checked. They are left exactly as they are. Guessing corrections
  to unverifiable external facts is precisely what produced the fabricated
  `go.sum` and the two images that named nothing; an unverified name that
  happens to be right beats a confident one that is wrong.
- **For the maintainer:** on a machine with registry access, `ollama show
  <name>` against each catalogued Ollama entry would settle it in one pass.
- **Validation:** 14 new tests (`tests/test_new_features_220.py`), including a
  guard that neither interface hardcodes the list again and that the MCP
  helper never raises; suite 3992/3992; gate GREEN twice serial and once
  parallel.

## AN. `make release` announced a pipeline that does not exist — ✅ fixed (Pass 219)

> **Status:** the release target now creates the GitHub Release itself when
> `gh` is present, refuses a dirty tree, and states plainly what it did *not*
> do when it could not do it.

- **Found by asking why publishing was ever manual.** The v1.7.0 Release object
  had been reported "blocked" for several passes. Rather than test the
  permission boundary a seventh time, the better question was why the
  repository's own release automation had never produced one.
- **It was documented as doing three things it cannot do:**

      release:  ## Tag and push (triggers CI → PyPI → Docker)
              @make release-check
              @git tag v$(VERSION)
              @git push --tags
              @echo "✓ v$(VERSION) released."

  There is no `.github/workflows/` directory in this repository — `.github/`
  holds issue templates, a PR template and `dependabot.yml`. So the tag
  triggered no CI, published to no PyPI, built no Docker image, and **created
  no GitHub Release**, which is exactly why the Releases tab sat at v1.6.0
  while `constants.py` and `CHANGELOG.md` both said 1.7.0. The final line
  printed `✓ released` for work that never happened. `ci` carried the same
  stale claim ("runs in GitHub Actions").
- **It would also have tagged a dirty tree.** When this was found, two files
  carried uncommitted changes; `make release` would have produced a v1.7.0 tag
  that omitted them, silently.
- **Repaired in the direction this session keeps choosing:** not by building a
  pipeline so the comment becomes true, but by deleting the false claim and
  making the command state reality. `release` now runs `gh release create
  v$(VERSION) --notes-file RELEASE.md` when `gh` is available, and when it is
  not it prints the remaining step and an explicit `✗ GitHub Release NOT
  created` rather than a checkmark. `release-check` refuses a dirty or staged
  tree, and verifies the version is in `CHANGELOG.md` and that `RELEASE.md` is
  non-empty before anything is tagged.
- **The regression guard is a property, not a substring ban.** The test skips
  itself if `.github/workflows/*.yml` ever appears, so the day someone adds a
  real workflow the claim becomes true and the test stops objecting — rather
  than forbidding the words forever.
- **Validation:** 18 new tests (`tests/test_new_features_219.py`); the
  dirty-tree refusal verified live (exited non-zero, left `git tag -l` at
  v1.6.0); suite 3996/3996.

## AO. The installer verified one Python and installed another — ✅ fixed (Pass 220)

> **Status:** `scripts/install.sh` pins the interpreter it verified, and is now
> testable at all — 20 tests where there were none.

- **Found by walking the first thing a user runs.** `curl … | bash` is the
  README's documented entry point, and nothing in 4,000 tests touched it.
- **The verification result was discarded.** The script searched
  `python3.13 → python3.12 → python3.11 → python3` for an interpreter new
  enough, printed it, and then wrote the wrapper with a *quoted* heredoc:

      sudo tee "$BIN_LINK" > /dev/null << 'EOF'
      #!/bin/bash
      export PYTHONPATH=/opt/aios:${PYTHONPATH:-}
      exec python3 -m aictl "$@"
      EOF

  `$PYTHON` never interpolates, so the wrapper always ran `python3`. On a
  machine where `python3` is 3.9 and `python3.11` exists — exactly the machine
  the search loop was written for — the installer printed
  `✓ Python: python3.11`, then `✓ Installation verified`, and left an `aictl`
  that failed on first use. Reproduced in a sandbox before fixing.
- **This is the gate's discarded command set again** (item AM), in the first
  thing a user touches rather than in a maintainer tool. Same shape: a correct
  check whose answer is computed and thrown away.
- **Three more, found while confirming the first:**
  - `cd "$INSTALL_DIR" && git pull` ran unprivileged against a clone made by
    `sudo git clone`, so **updating an existing install could not work** — and
    `set -euo pipefail` turned the failure into an abort. It also tested
    `-d "$INSTALL_DIR"`, so a non-git directory took the pull branch and died.
  - `if aictl --help` resolved through PATH, **verifying whatever PATH found
    first** rather than the file just written — an older install, or nothing
    when `/usr/local/bin` is off PATH.
  - `[ "$major" -ge 3 ] && [ "$minor" -ge 11 ]` compared the two numbers
    independently, rejecting a hypothetical 4.0 because 0 is not ≥ 11.
- **Why none of it was caught: the script had no seams.** A 100-line shell
  script that installs on import cannot be tested without a real install. It
  now exposes `detect_python` and `write_wrapper`, honours
  `AIOS_INSTALL_DIR` / `AIOS_BIN_LINK`, and runs `main` only when
  `AIOS_INSTALL_LIB` is unset — so `curl | bash` behaves exactly as before
  while the pieces are reachable from tests.
- **A recurring habit, now named.** The test asserting the old comparison was
  gone failed on the *comment quoting it* while explaining the bug — the fifth
  time this session a substring check caught prose instead of behaviour. The
  version floor is asserted across the 3.10/3.11 boundary instead.
- **Validation:** 20 new tests (`tests/test_new_features_220.py`), including an
  end-to-end check that the generated wrapper starts this checkout's aictl;
  suite 4002/4002.

## AP. The documented cost example could not be produced — ✅ fixed (Pass 221)

> **Status:** one USD formatter in `core/cost_per_call.py`; `sdk` and
> `cost_per_call` both delegate; `route cascade` sums numerically.

- **Found by running the README's quick start rather than reading it.** All
  five CLI commands worked. The Python snippet did not.
- **`print(r.cost)  # "$0.000047"` was unreachable.** Anything under $0.0001
  rendered as `f"${cost_usd * 1000:.4f}m"` — millidollars with a bare `m` — so
  0.000047 came out as **`$0.0470m`**. The documented output could not be
  produced for the documented value.
- **The unit was ambiguous, and the two copies disagreed about it.**
  `$0.0470m` reads as millions at least as readily as thousandths, and the
  second copy of the formula (`core/cost_per_call.py:190`) commented it
  "millicents" — which it is not. Two call sites, one formula, two different
  claims about what the number meant.
- **A free response printed `$0.0000m`.** That is the in-process mock, which is
  what zero-config gives a first-time user, so the malformed string was the
  most likely output anyone would ever see.
- **`route cascade` summed costs by string concatenation.** `total_cost` was
  initialised as a float, reassigned to `r1.cost` (a *string*), then
  `total_cost += r2.cost`. An escalating cascade reported
  **`$0.0000m$0.0000m`** — in the `--json` payload a script would parse as well
  as on screen. Reproduced before fixing:

      escalated : True
      total_cost: '$0.0000m$0.0000m'

- **Fixes.** One `format_usd()`: dollars, six decimals, no suffix to misread,
  `<$0.000001` when genuinely below display resolution rather than rounding a
  real cost to a confident zero. Costs accumulate numerically and are formatted
  once at the boundary, and the JSON payload gained `total_cost_usd` so callers
  never parse a currency string back into a number.
- **The habit, hit again.** The test asserting "millicents" was gone failed on
  the docstring that *explains* the wrong unit — the sixth substring check this
  session to catch prose instead of behaviour, and the second after naming the
  pattern in AM. It now parses both modules and asserts the milli formula is
  absent from executable code, docstrings stripped. Grepping source for a
  property about code is the habit; parsing is the fix.
- **Validation:** 21 new tests (`tests/test_new_features_221.py`); suite
  4023/4023.

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

## Sources (Part 4 — Pass 192)

KV offloading: [vLLM OffloadingConnector `cpu_bytes_to_use` PR
#24498](https://github.com/vllm-project/vllm/pull/24498) — the primary source for
the emitted schema; vLLM's own docs (docs.vllm.ai) and the 2026-01-08 connector
blog post were egress-blocked from this environment, so anything appearing only
there (`spec_name`, multi-tier specs) was left unimplemented rather than guessed.
Context on the wider design space (titles/abstracts only, arxiv.org also
egress-blocked): PEEK queue-informed KV cache management (arXiv:2607.02525),
KV cache management survey (arXiv:2607.02574), adaptive KV cache reuse
(arXiv:2605.24022).

Pass 193 — prefix reuse: KVFlow, *Efficient Prefix Caching for Accelerating
LLM-Based Multi-Agent Workflows* (NeurIPS 2025, arXiv:2507.07400) — LRU evicts
on past access time while agentic workflow structure already encodes future
execution order, so caches are dropped shortly before reuse; reported 1.12x /
1.08x speedups over SGLang / HiCache. Used as motivation only: aictl does not
implement KVFlow's steps-to-execution scheduling (it cannot control engine
eviction), it measures whether a deployment is in the high-reuse regime that
makes a larger cache tier worthwhile. Related workload characterization:
TraceLab, *Characterizing Coding Agent Workloads for LLM Serving*
(arXiv:2606.30560).
