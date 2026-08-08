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
