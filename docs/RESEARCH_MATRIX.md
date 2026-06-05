# aictl — Research Matrix (10 categories × ~10 sources → improvement points)

> Goal: このプロダクトのカテゴリーを10あげて、それぞれ10ずつ、arXiv / GitHub から
> 関連情報を集め改善点を洗い出す.
> Built incrementally — each pass fully populates a few categories with ~10 arXiv/GitHub
> references and the improvement points they imply for `aictl`. Complements
> `docs/IMPROVEMENTS.md` (which holds the prioritized narrative). Date: 2026-06-05.

## The 10 categories (mapped to aictl's module map)

| # | Category | Primary aictl modules | Status |
|---|----------|----------------------|--------|
| 1 | Inference engines & serving runtimes | `runtime/adapters,broker,health,nodes` | ✅ done |
| 2 | Model routing & LLM gateways | `cmd/route`, `stack/gateway`, `daemon/proxy` | ✅ done |
| 3 | RAG & retrieval | `core/rag` | ✅ done |
| 4 | Semantic caching | `core/sem_cache` | ✅ done |
| 5 | Guardrails, PII & prompt-injection safety | `core/guard,security,apikeys` | ✅ done |
| 6 | Quantization & model compression | `cmd/quant`, `runtime/optimize` | ✅ done |
| 7 | KV-cache management & request scheduling | `runtime/prefix_route,dynamo,autoscaler` | ⏳ pending |
| 8 | Speculative & accelerated decoding | `cmd/spec`, `runtime/speculative` | ⏳ pending |
| 9 | K8s orchestration, autoscaling & P/D disaggregation | `stack/*`, `runtime/mig,fabric` | ⏳ pending |
| 10 | Observability, cost/TCO, metering & FinOps | `metrics/*`, `cmd/tco`, `core/metering,cost` | ⏳ pending |

---

## Category 1 — Inference engines & serving runtimes

**Current aictl:** `runtime/adapters.py` detects **vLLM / SGLang / Ollama** only; `recommend`/`fit`
reason about a single GPU's VRAM.

**References (GitHub + arXiv):**
1. [vllm-project/vllm](https://github.com/vllm-project/vllm) — PagedAttention; 2000+ contributors, the de-facto baseline.
2. [sgl-project/sglang](https://github.com/sgl-project/sglang) — RadixAttention prefix cache; powers 400k+ GPUs.
3. TensorRT-LLM — NVIDIA low-level, leads optimized throughput at scale.
4. LMDeploy (TurboMind C++ backend) — lowest TTFT, eliminates Python overhead.
5. [kvcache-ai/ktransformers](https://github.com/kvcache-ai/ktransformers) — **heterogeneous CPU-GPU expert scheduling** (run huge MoE on limited VRAM).
6. llama.cpp / Ollama / LM Studio — **MLX** backend on Apple Silicon (faster than Metal).
7. [sihyeong/Awesome-LLM-Inference-Engine](https://github.com/sihyeong/Awesome-LLM-Inference-Engine) — engine taxonomy.
8. [lapp0/lm-inference-engines](https://github.com/lapp0/lm-inference-engines) — feature comparison matrix.
9. [xlite-dev/Awesome-LLM-Inference](https://github.com/xlite-dev/Awesome-LLM-Inference) — paper+code list (FlashAttention, PagedAttention, parallelism).
10. *Taming the Titans* survey [arXiv:2504.19720](https://arxiv.org/abs/2504.19720) + [H100 benchmarks](https://www.spheron.network/blog/vllm-vs-tensorrt-llm-vs-sglang-benchmarks/).

**Improvement points:**
- **Broaden adapters** to LMDeploy + TensorRT-LLM + LM Studio (all OpenAI/Triton-compatible) so every advisor (`recommend`/`optimize`/`route`) reflects the real 2026 field.
- **Heterogeneous CPU-GPU offload advisor** (ktransformers/llama.cpp pattern): `fit` currently fails models that don't fit VRAM; add CPU+GPU split / GGUF-offload recommendations.
- **Apple-Silicon/MLX detection + unified-memory math** in `runtime/broker.py` profile detection.
- **Engine capability matrix** in `constants.py` (which engine supports spec-decode method, guided-decoding backend, P/D-disagg) to drive advice instead of hardcoded assumptions.

## Category 2 — Model routing & LLM gateways

**Current aictl:** `cmd/route.py` is a hand-tuned regex complexity scorer; `daemon/proxy.py` +
`stack/gateway.py` provide a gateway, but no learned routing or multi-provider failover.

**References (GitHub + arXiv):**
1. [lm-sys/RouteLLM](https://github.com/lm-sys/routellm) + [arXiv:2406.18665](https://arxiv.org/abs/2406.18665) — preference-trained routers, ~2× cost cut.
2. [BerriAI/litellm](https://github.com/BerriAI/litellm) — 100+ providers, cost tracking, load-balancing, guardrails.
3. [aurelio-labs/semantic-router](https://github.com/aurelio-labs/semantic-router) — embedding-space routing, no LLM call.
4. [ulab-uiuc/LLMRouter](https://github.com/ulab-uiuc/LLMRouter) — 16+ routers, 11 datasets, unified CLI/eval.
5. [zilliztech/GPTCache] semantic cache as a routing-adjacent layer.
6. Portkey (Apache-2.0 since 2026) / Bifrost / Kong AI Gateway — production gateways with semantic cache.
7. BEST-Route [arXiv:2506.22716](https://arxiv.org/abs/2506.22716) — difficulty-aware multi-sampling, ~60% cost.
8. Dynamic routing & cascading survey [arXiv:2603.04445](https://arxiv.org/html/2603.04445v2).
9. Router robustness/fragility [arXiv:2504.07113](https://arxiv.org/html/2504.07113v1).
10. [Open-source LLM router comparison 2026](https://www.clawrouters.com/blog/best-open-source-llm-router).

**Improvement points:**
- **Optional learned router** (embedding-kNN / semantic-router style) with the regex scorer as zero-dep fallback; add **cascade/escalation** mode (BEST-Route).
- **Router eval harness** reusing `cmd/eval.py` + public datasets (LLMRouter ships 11) so the README's "~75% accuracy" becomes a tracked, reproducible metric.
- **Multi-provider failover & load-balancing** in `daemon/proxy.py` (LiteLLM parity) — health-aware endpoint selection using metrics already scraped.
- Heed the **fragility** finding: ship a confidence threshold + safe-default-to-large-model guard.

## Category 3 — RAG & retrieval

**Current aictl:** `core/rag.py` — SQLite store, **plaintext chunking**, **linear cosine scan**,
and a **64-dim SHA-256 hash fallback embedding** explicitly marked "NOT semantic" (`rag.py:321`).

**References (GitHub + arXiv):**
1. [deepset-ai/haystack](https://github.com/deepset-ai/haystack) — modular pipelines, **BM25** + dense hybrid.
2. **txtai** — SQLite+FAISS, **runs 100% locally**; the closest architectural peer to aictl's RAG.
3. LlamaIndex — vector/keyword/graph indexes over documents.
4. RAGFlow (InfiniFlow) — **deep document understanding** (PDF/table/scan parsing into chunks).
5. LangChain — retrieval orchestration baseline.
6. [Danielskry/Awesome-RAG](https://github.com/Danielskry/Awesome-RAG) — curated RAG app list.
7. CacheBlend (RAG-aware KV reuse) — from the scheduling survey.
8. [firecrawl best open-source RAG frameworks 2026](https://www.firecrawl.dev/blog/best-open-source-rag-frameworks).
9. [7 GitHub repos for mastering RAG](https://www.analyticsvidhya.com/blog/2025/10/github-repositories-for-mastering-rag-systems/).
10. Reranking + hybrid retrieval guidance (**dense+sparse boosts accuracy 20–30%**).

**Improvement points:**
- **Hybrid retrieval** (stdlib BM25/TF-IDF + cosine via reciprocal-rank fusion) — the +20–30% accuracy lever, and it works even with the hash fallback. *(top backlog item, see IMPROVEMENTS.md A)*
- **Real local embeddings** via engine endpoints (txtai parity), with the hash path *flagged* in `rag status`.
- **Cross-encoder reranking** of top-k via the local engine (optional).
- **Deep document parsing** (RAGFlow pattern): handle PDF/tables, not just decodable plaintext.
- **ANN index** instead of full linear cosine scan as corpora grow (stdlib IVF-style bucketing / LSH).

---

## Category 4 — Semantic caching

**Current aictl:** `core/sem_cache.py` — SQLite store, embeds via the same weak path as RAG,
matches on a **single global cosine similarity floor**, LRU eviction by `last_hit_at`.

**References (GitHub + arXiv):**
1. [zilliztech/GPTCache](https://github.com/zilliztech/GPTCache) — 6k★ semantic cache library, the baseline.
2. vCache — Verified Semantic Prompt Caching [arXiv:2502.03771](https://arxiv.org/abs/2502.03771): **per-prompt online-learned threshold** with user-defined error rate.
3. MeanCache [arXiv:2403.02694](https://arxiv.org/abs/2403.02694): user-centric/federated, **−83% storage**, +11% match speed, +17% F-score vs GPTCache.
4. *From Exact Hits to Close Enough* [arXiv:2603.03301](https://arxiv.org/html/2603.03301v1).
5. GPT Semantic Cache [arXiv:2411.05276](https://arxiv.org/pdf/2411.05276).
6. Asynchronous Verified Semantic Caching for tiered LLMs [arXiv:2602.13165](https://arxiv.org/html/2602.13165).
7. InstCache / GenerativeCache — generative cache, infinite-cache assumption (no eviction).
8. Portkey / Bifrost gateway semantic caches (30–50% cost cut claims).
9. CacheBlend — RAG-aware KV/prefix cache reuse.
10. LangChain / LlamaIndex cache backends (exact + semantic).

**Improvement points:**
- **Per-prompt adaptive (verified) threshold** (vCache): replaces aictl's single global floor that
  lives in the "similarity grey zone" where paraphrases and distinct intents overlap → fewer false
  hits. The online-learning estimator is pure-Python-feasible.
- **Embedding compression** (MeanCache): cut the SQLite store ~83% and speed matching.
- **Bounded-error verification / negative cache** so a wrong hit can't silently return.
- **Expose cache-decision quality** (precision / F-score, tokens-saved) as metrics, not just hit-rate.

## Category 5 — Guardrails, PII & prompt-injection safety

**Current aictl:** `core/guard.py` — 9 regex PII types + Luhn + 4 content policies, **literal,
single-turn, input-side** only.

**References (GitHub + arXiv):**
1. [guardrails-ai/guardrails](https://github.com/guardrails-ai/guardrails) — input/output guards framework.
2. [NVIDIA-NeMo/Guardrails](https://github.com/NVIDIA-NeMo/Guardrails) — programmable dialog rails.
3. [presidio-oss/hai-guardrails](https://github.com/presidio-oss/hai-guardrails) — PII + injection + leakage guards.
4. protectai/llm-guard — input/output scanners (PII, toxicity, injection).
5. Microsoft Presidio — entity recognition + reversible redaction.
6. LlamaFirewall [arXiv:2505.03574](https://arxiv.org/pdf/2505.03574) — open-source **agent** guardrail (Meta).
7. Llama Guard 3 / Prompt Guard — model-based input classifiers.
8. Guardrail evasion [arXiv:2504.11168](https://arxiv.org/html/2504.11168) — up to 100% bypass via **Unicode obfuscation / character smuggling**.
9. Adversarial prompt benchmarking [arXiv:2502.15427](https://arxiv.org/html/2502.15427v1) — ASR 60–92% across products.
10. [llm-guardrails topic](https://github.com/topics/llm-guardrails).

**Improvement points:**
- **Unicode NFKC normalization + homoglyph/character-smuggling folding** before matching — the
  benchmarks show 87–91% ASR exploits exactly this; cheap, pure-stdlib, highest ROI here.
- **Output-side PII redaction** with reversible tokens (Presidio parity) + more entity types.
- **Agent/tool-call guardrails** (LlamaFirewall pattern) wrapping the MCP server's tool I/O.
- **Adversarial benchmark harness** that replays public attack sets and tracks ASR per release.
- Optional **model-based check** (Llama Guard via local engine) behind a flag; regex stays the zero-dep default.

## Category 6 — Quantization & model compression

**Current aictl:** `cmd/quant.py` advises FP16/FP8/Q8/**AWQ**/Q4/Q3 on "April 2026 empirical" data.

**References (GitHub + arXiv):**
1. [vllm-project/llm-compressor](https://github.com/vllm-project/llm-compressor) — **FP4 (MXFP4 + NVFP4)**, vLLM-native export.
2. [intel/auto-round](https://github.com/intel/auto-round) — SOTA low-bit, MXFP4/NVFP4, **CPU/XPU/CUDA**, vLLM+SGLang compatible.
3. [ModelCloud/GPTQModel](https://github.com/modelcloud/gptqmodel) — AWQ/GPTQ/FP8/GGUF/EXL3 configs, HW-accelerated.
4. [casper-hansen/AutoAWQ](https://github.com/casper-hansen/AutoAWQ) — **officially deprecated / unmaintained** (aictl still names AWQ as a primary path).
5. [AutoGPTQ/AutoGPTQ](https://github.com/AutoGPTQ/AutoGPTQ) — classic GPTQ.
6. [pprp/Awesome-LLM-Quantization](https://github.com/pprp/awesome-llm-quantization).
7. bitsandbytes — 8/4-bit runtime quant.
8. GGUF / llama.cpp — **Q4_K_M** community sweet spot (92% quality, 75% smaller).
9. NVFP4 / MXFP4 4-bit microscale formats (Blackwell-class HW).
10. AWQ + GPTQ original papers (activation-aware / second-order weight quant).

**Improvement points:**
- **Add FP4 (NVFP4/MXFP4) rows** with per-engine support flags — now mainstream in
  llm-compressor / AutoRound / GPTQModel.
- **Refresh tooling references**: AutoAWQ is deprecated → point to llm-compressor / GPTQModel /
  AutoRound, and surface the **export format per engine** (GGUF/AWQ-Marlin/GPTQ).
- **Surface the Q4_K_M sweet-spot** call-out in `quant recommend`.
- **CPU/XPU quant path** (AutoRound) — ties into the heterogeneous-offload advisor (Cat 1).

---

## Pending (next loop iterations)

Categories 7–10 remain (≈10 arXiv/GitHub refs + improvement points each): KV-cache/scheduling,
speculative decoding, K8s/disaggregation, and observability/FinOps. Each is backed by narrative
analysis already in `docs/IMPROVEMENTS.md` (Parts 1–2).
