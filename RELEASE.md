# Release v1.7.0

## Highlights

- **3,433+ tests** (Python + Go), zero failures — run with `aictl gate`
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

## Upgrade notes

Fully backward-compatible with v1.6.0. Every feature above defaults to disabled/empty; existing configs, scripts, and zero-config workflows are unaffected. No external Python dependencies were added.
