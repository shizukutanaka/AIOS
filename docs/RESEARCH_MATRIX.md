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
| 7 | KV-cache management & request scheduling | `runtime/prefix_route,dynamo,autoscaler` | ✅ done |
| 8 | Speculative & accelerated decoding | `cmd/spec`, `runtime/speculative` | ✅ done |
| 9 | K8s orchestration, autoscaling & P/D disaggregation | `stack/*`, `runtime/mig,fabric` | ✅ done |
| 10 | Observability, cost/TCO, metering & FinOps | `metrics/*`, `cmd/tco`, `core/metering,cost` | ✅ done |

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

## Category 7 — KV-cache management & request scheduling

**Current aictl:** `runtime/prefix_route.py` (prefix-hash locality), `runtime/dynamo.py` (Dynamo
KVBM + NIXL), `runtime/autoscaler.py`. No tiered KV offload or KV-budget-aware scheduling.

**References (GitHub + arXiv):**
1. [LMCache/LMCache](https://github.com/LMCache/LMCache) + [arXiv:2510.09665](https://arxiv.org/abs/2510.09665) — KV out of GPU, **shared across engines/queries**, PD-disagg transfer.
2. [kvcache-ai/Mooncake](https://github.com/kvcache-ai/Mooncake) + [arXiv:2407.00079](https://arxiv.org/abs/2407.00079) — distributed CPU+SSD global KV cache; **FAST'25 Best Paper**.
3. [vllm-project/production-stack](https://github.com/vllm-project/production-stack) — **KV-cache-aware router** + Prometheus/Grafana.
4. KVcached (Yu et al., 2025) — KV across heterogeneous memory tiers.
5. Tutti [arXiv:2605.03375](https://arxiv.org/html/2605.03375) — **SSD-backed** KV for long context.
6. TraCT [arXiv:2512.18194](https://arxiv.org/html/2512.18194v1) — **CXL shared-memory** KV at rack scale.
7. CacheFlow [arXiv:2604.25080](https://arxiv.org/html/2604.25080v1) — 3D-parallel KV restoration.
8. Online scheduling with KV constraints [arXiv:2502.07115](https://arxiv.org/html/2502.07115v5).
9. Locality-aware fair scheduling (DLPM) [arXiv:2501.14312](https://arxiv.org/html/2501.14312v1).
10. NVIDIA Dynamo KVBM + NIXL (already integrated by aictl).

**Improvement points:**
- **Tiered KV-offload advisor** (LMCache/Mooncake): aictl has KVBM but no advice on CPU/SSD/CXL KV
  tiers for cross-query prefix reuse + PD cache transfer — add to `deploy optimize`/`disagg`.
- **KV-cache-aware router** (production-stack parity): extend `prefix_route` soft-score with a
  **KV-budget** term and **DLPM locality** so dispatch preserves prefix reuse without starving tenants.
- **SSD / long-context KV** recommendations (Tutti) keyed by context length.
- Offer **LMCache** as a recommended layer in `modelservice`/`disagg` exports.

## Category 8 — Speculative & accelerated decoding

**Current aictl:** `cmd/spec.py` only pairs draft+target models (classic), capped at 3× speedup.

**References (GitHub + arXiv):**
1. [SafeAILab/EAGLE](https://github.com/SafeAILab/EAGLE) + EAGLE-3 [arXiv:2503.01840](https://arxiv.org/html/2503.01840v1) — **4.79×**, de-facto standard, vLLM+SGLang.
2. [FasterDecoding/Medusa](https://github.com/FasterDecoding/Medusa) — multi-head parallel draft.
3. [sgl-project/SpecForge](https://github.com/sgl-project/SpecForge) + [arXiv:2603.18567](https://arxiv.org/pdf/2603.18567) — drafter **training** framework.
4. DeepSeek-V3 **multi-token prediction (MTP)** — pretraining-time spec heads.
5. vLLM `--speculative-config` (method=eagle3/medusa/mtp).
6. SGLang EAGLE integration.
7. Lookahead decoding (n-gram, draft-free).
8. SSSD: Simply-Scalable Speculative Decoding [arXiv:2411.05894](https://arxiv.org/html/2411.05894).
9. Semi-clairvoyant scheduling of spec requests [arXiv:2505.17074](https://arxiv.org/pdf/2505.17074).
10. Variational Speculative Decoding [arXiv:2602.05774](https://arxiv.org/html/2602.05774v1).

**Improvement points:**
- Add an **EAGLE-3 / Medusa / MTP / lookahead method dimension** to the advisor with an engine
  support matrix and correct flags; recommend EAGLE-3 when a trained head exists. *(IMPROVEMENTS.md L)*
- **Raise the speedup ceiling** (4.79× evidence vs the current hard 3.0 cap in `spec.py:41`).
- **Spec-request scheduling** awareness (semi-clairvoyant) for the governor under mixed load.
- Point users to **SpecForge** when no drafter exists for their model family.

## Category 9 — K8s orchestration, autoscaling & P/D disaggregation

**Current aictl:** `stack/*` exports 7 formats (KServe, Gateway API, KEDA, HPA, Dynamo, P/D
Disagg, ModelService) + `runtime/mig,fabric`. Strong, but missing newer operators.

**References (GitHub + arXiv):**
1. [kserve/kserve](https://github.com/kserve/kserve) — **LLMInferenceService** (v0.16/0.17).
2. [llm-d/llm-d](https://github.com/llm-d/llm-d) — CNCF-sandbox disaggregated + prefix-cache data plane.
3. [kubeai-project/kubeai](https://github.com/kubeai-project/kubeai) — **scale-from-zero**, no Istio, lightweight operator.
4. [vllm-project/aibrix](https://github.com/vllm-project/aibrix) — ByteDance; **StormService** P/D disagg + **high-density LoRA**.
5. [ai-dynamo/dynamo](https://github.com/ai-dynamo/dynamo) — datacenter-scale, LeaderWorkerSet, disagg.
6. vLLM production-stack — KV-aware router + observability bundle.
7. K8s Gateway API **InferencePool** (already exported by aictl).
8. KEDA / HPA autoscaling baselines.
9. Ray Serve GPU scheduling ([2026 guide](https://blog.premai.io/deploying-llms-on-kubernetes-vllm-ray-serve-gpu-scheduling-guide-2026/)).
10. Disaggregation pragmatics [arXiv:2506.05508](https://arxiv.org/html/2506.05508v1) + AFD [arXiv:2605.28302](https://arxiv.org/html/2605.28302v1).

**Improvement points:**
- **Add AIBrix StormService + KubeAI exports** to `stack/` (8th/9th formats) to widen platform reach.
- **Scale-from-zero advisor** (KubeAI pattern) layered on the KEDA/HPA exports.
- **High-density LoRA serving export** (AIBrix) surfacing `runtime/lora` to K8s.
- Validate all exports against **KServe v0.17 LLMInferenceService** latest schema in CI.

## Category 10 — Observability, cost/TCO, metering & FinOps

**Current aictl:** `metrics/{otel,slo,prometheus,genai_spans}` (OTel GenAI SemConv), `cmd/tco`,
`core/{metering,cost}`. Emits spans but not validated against the popular OTLP consumers, and
several value-prop metrics aren't emitted.

**References (GitHub + arXiv):**
1. [traceloop/openllmetry](https://github.com/traceloop/openllmetry) — OTel-based, **40+ auto-instrumentations**.
2. [langfuse/langfuse](https://github.com/langfuse/langfuse) — OTel-native observability/evals/prompt-mgmt (now ClickHouse).
3. [helicone/helicone](https://github.com/helicone/helicone) — one-line LLM observability.
4. Arize Phoenix — self-hosted tracing/eval.
5. OpenLIT — OTel GenAI, Go/Java coverage.
6. OpenCost / Kubecost — **GPU cost allocation**.
7. OTel **GenAI SemConv** (already followed by aictl).
8. FREESH energy/carbon scheduling [arXiv:2511.00807](https://arxiv.org/pdf/2511.00807).
9. vLLM Prometheus/Grafana dashboards (`vllm:` metrics).
10. [Self-hosted LLM observability guide 2026](https://www.spheron.network/blog/llm-observability-gpu-cloud-langfuse-arize-phoenix-helicone/).

**Improvement points:**
- **Validate OTLP export against Langfuse / OpenLLMetry / Phoenix** so aictl users get standard
  dashboards for free — a big adoption lever for an already-OTel-native tool.
- **Emit the ROI metrics** `aios.cache.tokens_saved`, `aios.route.cost_saved`,
  `aios.guard.redactions` so FinOps dashboards *prove* the savings competitors only assert. *(IMPROVEMENTS.md J)*
- **GPU cost allocation per tenant** (OpenCost parity) via existing `core/metering`.
- **Carbon/energy in TCO** (FREESH): report kWh + CO₂e and GPU power-cap advice alongside dollars.
- **Prompt-management / eval surface** (Langfuse parity) tying `cmd/eval` + `cmd/prompt` to traces.

---

## Synthesis — cross-category themes

Three patterns recur across all 10 categories and point at the highest-leverage work:

1. **The embedding path is load-bearing and currently fake offline.** It silently degrades RAG
   (Cat 3), the semantic cache (Cat 4), and any future learned router (Cat 2). Fixing it —
   stdlib hybrid retrieval now, real engine-embeddings + a *flagged* fallback — unblocks three
   categories at once. **#1 priority.**
2. **aictl is an advisor; the frontier moved, so the advice is stale.** Engines (Cat 1: LMDeploy/
   TRT-LLM/MLX), spec-decode (Cat 8: EAGLE-3), quant (Cat 6: FP4, AutoAWQ deprecated), and K8s
   (Cat 9: AIBrix/KubeAI) all advanced. Refreshing tables + adding adapters is low-risk,
   high-coverage, and fits the zero-dep model perfectly.
3. **The value props aren't measured.** Cache savings, route savings, fairness, carbon, guard
   redactions (Cats 4/5/7/10) are claimed but not emitted as metrics or verified against attack
   sets. Making them observable (OTLP + the `aios.*` counters) turns assertions into evidence.

All improvement points are **zero-external-dep feasible** and land via aictl's standard flow
(`cmd/*`/`runtime/*` + tests + `aictl gate`). This matrix backs the prioritized narrative in
`docs/IMPROVEMENTS.md`; the single strongest next implementation remains **hybrid RAG retrieval
+ flagging the hash-embedding fallback** (Cat 3 / IMPROVEMENTS.md A).
