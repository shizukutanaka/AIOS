# Release v1.7.0

## Highlights

- **3,814+ tests** (Python + Go), zero failures — run with `aictl gate`
- **Zero external Python dependencies** — stdlib only
- **80 Python + 29 Go CLI commands**
- **30 REST API endpoints**
- **19 MCP tools** for Claude Code / Cursor integration (now with progress notifications)
- **7 K8s export formats** including llm-d ModelService
- All new features are **off-by-default / opt-in** — upgrading from v1.6.0 changes no behavior until you enable something

## What's New

### Retrieval quality (RAG)
- **Hybrid retrieval**: dense cosine + lexical BM25 fused with Reciprocal Rank Fusion — usable even when only the offline hash-embedding fallback is available.
- **Pluggable reranker** (`aictl rag search --rerank`): optional TEI-compatible `/rerank` cross-encoder stage over a widened candidate pool; degrades silently to RRF order if unreachable.
- **Embedding capability detection**: probes the engine's `/v1/models` and picks the best available embedding model instead of guessing, with honest degraded-mode flags in `rag status` / `cache status`.

### Layered routing
- **Embedding-kNN tie-breaker** (`aictl route ... --knn`): a confidence-gated middle layer between the regex scorer and the cascade tail — consulted only near a tier boundary, always abstains to the regex verdict on any uncertainty.

### Guardrails (security)
- **Proxy-integrated guard**: content-policy checks and PII output-redaction now run on real inference traffic (opt-in via `guard_policy` / `guard_redact_output`, no-op by default).
- **Optional model check** with an LRU verdict cache: point the guard at a local Llama-Guard-style model; the cache neutralizes the guardrail-as-DoS-target amplification vector (arXiv:2606.14517).

### MCP server
- **2026-07-28 spec compatibility**: protocol-version negotiation, `server/discover`, `ttlMs`/`cacheScope` on listings.
- **Progress notifications** for long-running tool calls (opt-in per-request `progressToken`).

### Fairness & cost
- **Fair-share advisory** (`aictl tco fairshare`): Jain's fairness index over per-tenant/apikey token usage, with starved/over-share classification. Advisory only — does not touch the serving path.
- **Carbon/energy advisor** (`aictl tco carbon`): kWh + CO₂e, GPU power-cap flags, FREESH-style savings projections.

### Engine conformance
- **`aictl engines conform [url]`**: probes the six HTTP surfaces aictl depends on
  (`/v1/models`, reachability, chat completions, streaming, `/v1/embeddings`,
  `/metrics`) and maps each to *which aictl features work, degrade, or break*.
  Closes a real gap: `selftest` never contacted an engine and the test suite
  exercises only the bundled mock, so non-conformance previously surfaced
  mid-request as silent quality loss — an engine without `/v1/embeddings` makes
  RAG and the semantic cache fall back to the non-semantic hash embedding.
  Read-only, and an unreachable engine still yields a full report.

### KV prefix-cache offloading
- **`aictl deploy optimize <model> --kv-offload`**: advises on vLLM's
  OffloadingConnector, which extends the prefix cache into pinned host memory so
  an evicted prefix stays a cache hit instead of a recompute. Matters because
  `--enable-prefix-caching` alone is bounded by leftover VRAM, which thrashes on
  prefix-heavy workloads (multi-turn chat, shared RAG system prompts, agent loops).
- Sizing is treated as a **safety property**: `cpu_bytes_to_use` is pinned,
  unswappable host memory, so the advisor takes at most 25% of host RAM, keeps an
  8GB floor, and refuses outright rather than guessing when host RAM is unknown.
- Declines with a stated reason when it would not help — small model on a large
  GPU, non-GPU host, or measured prefix reuse under 10%. It does **not** make a
  model that exceeds VRAM fit, and says so.
- **Measured, not assumed**: the prefix router now keeps hit/miss accounting
  (`reuse_rate()`), so the decision uses observed traffic when the process has
  served any. Motivated by KVFlow (NeurIPS 2025, arXiv:2507.07400) on LRU
  eviction discarding caches shortly before reuse in agentic workflows.

### Structured output
- **`aictl guided lint <schema>`**: reviews a JSON Schema's *design*, not just
  whether a document matches it. Constrained decoding guarantees format, not
  semantics — a valid schema can compile fine and still make answers worse. The
  main check is field ordering: generation is autoregressive, so a schema
  emitting `answer` before `reasoning` forces the model to commit to a
  conclusion and then rationalize it. Also flags deep nesting, very wide
  schemas, undescribed fields, and optional fields that cannot be null.

### Honest degradation
- **Mock responses are now disclosed.** With no engine running, the SDK starts
  an in-process mock so zero-config works. It previously attributed that text
  to a real model name and reported a cost for inference that never happened.
  Responses now carry `mock=True`, name the model `"mock"`, report zero cost,
  and say `MOCK` in their repr; `ai.status` gained a `mock` key.
- **`engines conform` flags plaintext HTTP** to a non-loopback engine, where
  the `Authorization` header and every prompt cross the network in cleartext.
  Loopback is exempt.
- **RAG documents are screened for injected instructions** at both ingest and
  retrieval (`rag_screen_policy`, default off), including payloads hidden with
  zero-width and bidi characters — invisible to a human reviewing the document,
  fully tokenized by the model.

### Advisors that admit their limits
- **Quantization**: when an FP4 format wins on a Blackwell card, the
  recommendation now says FP8 also fits at higher quality and that FP4 should
  be validated on your own workload. Fires only when FP4 wins and FP8 fits.
- **Speculative decoding**: flags draft-token counts outside the useful 3–8
  band, and warns that an EAGLE3 head pointed at a fine-tune loses acceptance.

### Developer experience
- **`aictl gate --parallel`** runs the suite file-per-process: gate drops from
  ~59s to ~30s. Serial remains the source of truth. Each worker gets its own
  state directory, so the suite no longer touches your real `~/.aios`.
- **Doc counts maintain themselves.** `gate` derives the test/file counts in
  `CLAUDE.md` and `RELEASE.md` rather than trusting hand-edited numbers.

### Catalog & advisors
- New models (GLM-5.2, Kimi K2.6); Medusa speculative-decoding method; vLLM v0.19 CPU KV-offload hints; NVFP4 quant sweet-spot notes; Apple-Silicon unified-memory fit math; 3 new engine adapters (LMDeploy, TensorRT-LLM, LM Studio — all opt-in, OpenAI-compatible).

## Install

```bash
git clone https://github.com/shizukutanaka/aios.git
cd aios
python3 -m aictl demo --auto     # no GPU needed
python3 -m aictl gate            # compile + import + version + tests + demo
```

## Requirements

- Python 3.11+
- Linux (any distro)
- Optional: Podman, NVIDIA GPU, Ollama / vLLM / SGLang

## For contributors

`docs/REVIEW_v1.7.0.md` records this release's strengths, weaknesses, and a
prioritized backlog — every item grounded in real code rather than aspiration.
`docs/INSTRUCTIONS_OPUS.md` and `docs/INSTRUCTIONS_SONNET.md` are playbooks for
design-scope and mechanical-scope contribution sessions respectively.

## Upgrade notes

Fully backward-compatible with v1.6.0. Every feature above defaults to disabled/empty; existing configs, scripts, and zero-config workflows are unaffected. No external Python dependencies were added.
