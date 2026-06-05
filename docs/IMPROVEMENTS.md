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
(`core/guard.py`), 7 K8s export formats, Dynamo/MIG/LoRA, and an 18-tool MCP server.
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
  3. **Cheap reranker:** optional cross-encoder rerank via the local engine for top-k.

## B. Semantic cache — correctness & cache-aware reuse

- **Current:** `core/sem_cache.py` keys on the same weak embedding; eviction is LRU by
  `last_hit_at` (now parameter-bound after the v1.6 fix). No notion of cache-aware RAG
  reuse or partial-prefix reuse.
- **SOTA:** CacheBlend / prefix-cache reuse for RAG contexts (scheduling survey); Portkey
  reports 30–50% cost cuts from semantic caching with <100ms hits.
- **Proposal:** (a) record and expose **cost-saved-per-route** and cache-hit histograms in
  `metrics/` (OTel + Prometheus) so the 30–50% claim is *measured*, not assumed; (b) add a
  `--similarity-floor` guard and per-model namespacing audit to prevent cross-model false hits.

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
  2. **Cascade mode:** try the small model, escalate to the large model only if a local
     confidence/verifier check fails — a research-backed superset of the current one-shot route.
  3. Ship a **router eval harness** (extend `cmd/eval.py`) so accuracy is a tracked number.

## D. Inference-engine coverage — three engines, the field has six

- **Current:** `runtime/adapters.py` detects **vLLM / SGLang / Ollama** (per
  `runtime/CLAUDE.md`).
- **Peers:** 2026 comparisons treat **TensorRT-LLM, LMDeploy (TurboMind), LM Studio**, and
  **MLX on Apple Silicon** as mainstream; SGLang/LMDeploy show ~29% higher throughput than
  vLLM, TensorRT-LLM leads at scale, LMDeploy has lowest TTFT.
- **Proposal:** add adapters + health/metric scrapers for **LMDeploy** and **TensorRT-LLM**
  (both expose OpenAI/Triton endpoints), an **LM Studio** adapter (OpenAI-compatible), and
  **MLX/Apple-Silicon detection** in `runtime/broker.py` profile detection (currently
  nvidia→amd→intel→npu→cpu). This widens `recommend`/`optimize`/`route` to the real field.

## E. KV-cache-aware cluster routing & long-context KV

- **Current:** `runtime/prefix_route.py` does prefix-hash locality (RadixAttention-style),
  good. No KV-budget-aware scheduling or long-context KV compression advice.
- **SOTA:** *Online Scheduling with KV Cache Constraints* (arXiv:2502.07115); KV-cache
  optimization survey (eviction/compression: H2O, StreamingLLM, SnapKV); InfiniGen KV
  prefetch.
- **Proposal:** (a) extend the router's soft-score with a **KV-budget term** (reject/penalize
  endpoints near KV exhaustion using metrics aictl already scrapes); (b) add a
  **long-context KV advisor** to `cmd/optimize.py` recommending eviction/compression flags
  (e.g. vLLM cache settings) by context length — advisory-only, zero-dep.

## F. Prefill/decode & MoE serving advisors

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
     closes the cheapest evasions, pure stdlib.
  2. **Output-side PII redaction** pass (not just input scan) and a redaction mode.
  3. **Optional model-based check hook** (Llama Guard via the local engine) behind a flag,
     keeping the regex layer as the zero-dep default.
  4. Multi-turn/session context for injection heuristics.

## H. Quantization advisor — refresh to the 2026 frontier

- **Current:** `cmd/quant.py` advises FP16/FP8/Q8/AWQ/Q4/Q3 on "April 2026 empirical" data.
- **Field:** Q4_K_M is the community sweet spot (92% quality / 75% smaller); **NVFP4 / MXFP4
  (4-bit float)** and updated AWQ/GPTQ kernels are now mainstream on Blackwell.
- **Proposal:** add **FP4 (NVFP4/MXFP4)** rows to the quant table and engine-specific support
  flags; surface the Q4_K_M "sweet spot" call-out in `quant recommend`.

## I. Apple Silicon / unified-memory path

- **Current:** profile detection is GPU/NPU/CPU; no MLX/Metal path. Peers (Ollama, LM Studio)
  moved to **MLX** on M-series as the faster default.
- **Proposal:** detect Apple Silicon + unified memory in `runtime/broker.py`/`recommend.py`,
  and have `fit`/`recommend` reason about **unified memory** (model can use system RAM as VRAM),
  which today's VRAM-only math gets wrong on Macs.

## J. Observability of the value props

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
