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
| 4 | Semantic caching | `core/sem_cache` | ⏳ pending |
| 5 | Guardrails, PII & prompt-injection safety | `core/guard,security,apikeys` | ⏳ pending |
| 6 | Quantization & model compression | `cmd/quant`, `runtime/optimize` | ⏳ pending |
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

## Pending (next loop iterations)

Categories 4–10 will be filled the same way (≈10 arXiv/GitHub refs + improvement points each):
semantic caching, guardrails/safety, quantization, KV-cache/scheduling, speculative decoding,
K8s/disaggregation, and observability/FinOps. Several already have narrative analysis in
`docs/IMPROVEMENTS.md` (Parts 1–2) that this matrix will back with concrete source links.
