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

## L. Speculative-decoding advisor is a generation behind

- **Current:** `cmd/spec.py` only pairs a **draft + target model** (classic spec decoding);
  its docstring tops out at "2–3× speedup". No EAGLE/Medusa/MTP awareness.
- **SOTA:** **EAGLE-3** (arXiv:2503.01840) is now the **de-facto industrial standard** — up to
  **4.79× on Llama-3.3-70B with no quality loss**, supported by vLLM and SGLang; **Medusa**
  (multi-head) and **DeepSeek-V3 multi-token-prediction (MTP)** are mainstream; SpecForge
  (2603.18567) trains drafters.
- **Proposal:** extend the advisor with a **method dimension** — `draft-pairing | EAGLE-3 |
  Medusa | MTP` — each with an engine-support matrix and correct flags (e.g. vLLM
  `--speculative-config '{"method":"eagle3",...}'`). Recommend EAGLE-3 when a trained head
  exists for the family; fall back to draft-pairing otherwise. Raise the speedup ceiling.

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

- **Current:** a 19-tool JSON-RPC MCP server (`aictl/mcp_server`). aictl ships OTel GenAI
  spans (`metrics/genai_spans.py`) elsewhere, but the MCP path doesn't emit per-tool spans or
  stream progress.
- **Field:** the 2026 agent frameworks (Claude Agent SDK, LangGraph, OpenAI Agents SDK, AWS
  **Strands**) all converge on **MCP + OTel + streaming + persistence**; Strands in particular
  plugs straight into any OTel backend. Observability is now the differentiator, not tool count.
- **Proposal:** (1) emit an **OTel span per MCP tool call** (reuse `genai_spans.py`) so aictl is
  a first-class OTel-observable MCP backend; (2) add **streaming/progress notifications** for
  long tools (deploy, bench); (3) optional **session persistence** for multi-step agent flows.

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
