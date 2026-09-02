# Release v1.7.0

## Highlights

- **4,139+ tests** (Python + Go), zero failures — run with `aictl gate`
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

### Fixed
- **The Go port builds.** It did not, from a clean checkout, in any previous
  release. `go.sum` recorded four checksums that no hash function had produced —
  each sharing a long prefix with the real value and then diverging into a
  plausible tail, one of them 43 base64 characters and so not a SHA-256 at all.
  Go refused with `SECURITY ERROR`, which read like an attack and was really the
  toolchain correctly declining to trust a file that had never attested to
  anything. Every entry has now been verified against `sum.golang.org` and is
  pinned in the test suite; the compile error the checksum failure had been
  masking (an unused import) is fixed. `go build`, `go vet` and `go test ./...`
  are clean, and `aictl gate` checks the Go port on every run.
- **`AIOS_STATE_DIR` and `--state-dir` now move all of your state.** They
  previously moved some of it — see **Upgrade notes**, which you should read if
  you set either.
- **The state directory is created owner-only (`0700`).** It holds your cloud
  API key, the metering ledger, the audit log and every document indexed into
  RAG, and was created with the process umask — typically world-readable. The
  security scanner had been reporting this as HIGH and printing `chmod 700` as
  advice rather than doing it. Existing loose directories are tightened in
  place, and never loosened.
- **`aictl gate` no longer writes into your real `~/.aios`.** 53 of the 280 test
  files did.
- **Fair-share now measures a rolling window, not all time.** The admission
  gate compared *cumulative* usage since an entity first appeared, so a tenant
  that was heavy last month kept being deferred long after it stopped
  contending. It now measures the last hour by default
  (`fair_share_window_seconds`); set it to `0` for the old behaviour.
  `aictl tco fairshare --window SECONDS` applies the same to the report, which
  still defaults to cumulative.
- **`ai.classify()` tells you when it did not actually classify.** When the
  model's answer matched none of your categories it returned the first one
  silently, indistinguishable from a confident result — the shipped example
  labelled every message, including complaints, "positive". The return value is
  still a plain string, now carrying `.matched` (and `.mock`) for callers who
  want to check.
- **The SDK examples run.** None of the five could be executed as documented
  (`import aictl` failed from a clone), `05_cost.py` called a property as a
  method, and `02_extract.py` needs a real engine without saying so.
- **The daemon's OpenAPI spec covers the whole API.** It documented 26 of the
  30 `/v1/` endpoints — the five missing ones being mostly POSTs
  (`/v1/stacks/apply`, `/v1/stacks/down`, `/v1/models/register`,
  `/v1/recipes/run`, `/v1/scheduler`), i.e. the state-changing half. Its
  version also read 1.5.0, two releases behind; `aictl gate` now checks it
  alongside `constants.py`, `pyproject.toml` and the Go port.
- **`aictl config set` now rejects invalid values.** It type-coerced and saved
  without ever running the validation that `config validate` and `config
  import` use, so `aictl config set fair_share_policy bogus` was accepted — and
  the gate reads an unrecognised policy as neither "off" nor "enforce", which
  silently downgraded enforcement to a warning.
- **Fair-share now covers embeddings, and its 503 is retryable.** With
  `fair_share_policy` enabled, `/v1/embeddings` was exempt from the gate, so a
  deferred entity could keep consuming the same GPU through that path. It is
  now gated at the same point in the pipeline as completions, and both send the
  `Retry-After` header the code had always claimed to send.
- **The documented MCP setup now works.** The README had two contradictory
  `## MCP Server` sections; the more inviting one showed a config that was not
  valid JSON (a `//` comment inside a ```json fence) and omitted the
  `mcpServers` wrapper, so no host would load it. The server itself was always
  fine. There is now one correct section, a copy-pasteable config matching the
  `claude_desktop_config.json` this repo ships, and a one-line command to
  verify the server without a host.
- **The Docker Quick Start works.** `docker compose up -d` previously failed
  four independent ways: the compose `command:` was appended to the
  Dockerfile's `aictl` ENTRYPOINT and died on an argparse error; `--auto` runs
  the demo *then exits*, so the container was dead on arrival; both servers
  bound loopback, unreachable from the host; and the mock engine's port was
  never published. `aictl demo` gained `--host` (default loopback — nothing in
  the demo is authenticated), and the compose file now matches what runs.
- **Per-call costs are readable again.** Anything under $0.0001 printed as
  millidollars with a bare `m` suffix (`$0.0470m`), which reads as millions as
  easily as thousandths — and a free response printed `$0.0000m`. Costs now
  render in dollars to six decimals, or `<$0.000001` when genuinely below that.
  `aictl route cascade` was also summing costs by string concatenation, so an
  escalating cascade reported two price tags glued together; it now accumulates
  numerically and its `--json` output carries `total_cost_usd`.
- **The one-line installer no longer installs the wrong Python.** It searched
  for an interpreter new enough to run aictl, printed the one it found, and
  then wrote a wrapper hardcoded to `python3` — so on a system where `python3`
  is 3.9 and `python3.11` is present, it reported success and left a broken
  `aictl`. It now pins the interpreter it verified. Updating an existing
  install also works (`git pull` ran without the privileges the clone was made
  with), and the post-install check verifies the file it just wrote rather than
  whatever `aictl` PATH happens to resolve.
- **`make release` does what it says.** It was documented as triggering
  CI → PyPI → Docker and printed `✓ released`; the repository has no
  `.github/workflows/` at all, so it triggered nothing and never created a
  GitHub Release. It now publishes the release from `RELEASE.md` via `gh`,
  refuses to tag a dirty tree, and says explicitly when a step did not run.
- **Shell completions cover every command.** They were three hand-written lists
  — bash 38 names, zsh 17, fish 38 — against a surface of 80, so most of the CLI
  had no tab completion and `aictl model <TAB>` knew five of its eight
  subcommands. All three are now generated from the registered parser, with
  per-command descriptions and nested subcommands. Re-run
  `aictl completion <shell>` to pick them up.
- **`aictl help advanced` tells the truth about its own size.** It advertised
  "the full 65-command surface" while aictl shipped 80, and listed commands in a
  hand-maintained table. It now generates the listing from the parser.
- **Engine images are pinned, and two of them were unpullable.** `aictl apply`
  emitted `vllm/vllm-openai:latest` while `aictl deploy modelservice` pinned
  `v0.19.0`, so the same product deployed different vLLM builds depending on
  which path you took — and the local one changed under you without warning.
  All engine paths now share one pinned map. Two constants also turned out to
  name images that do not exist: `lmsys/sglang` (the org is `lmsysorg`) and
  `ollama/ollama:0.20` (the tag is `0.20.0`). Both were unused, which is why
  nothing had broken. Generated Quadlet units and KServe CRDs now name a
  specific build, which is what makes them verifiable by `aictl trust`.

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

Every *feature* above defaults to disabled/empty, and no external Python
dependencies were added. Zero-config workflows — the default `~/.aios` — are
unaffected, and you can upgrade without doing anything.

**One behaviour change, and it affects you only if you set `AIOS_STATE_DIR`,
`AICTL_STATE_DIR`, or `--state-dir`.** Read this before upgrading if you do.

Those settings used to move only *part* of the state. `perf.jsonl`, the
semantic cache and the RAG index followed them; `state.json`, the model
registry, your API keys, the audit log and the metering ledger did not, and
stayed in `~/.aios`. The state was silently split across two directories.

v1.7.0 resolves the directory in one place, so **everything** now follows the
setting. That is the fix — but it means the files that used to stay behind are
no longer where `aictl` looks. Without migrating, your node config, registered
models, API keys and audit history will appear empty.

`aictl` detects this on startup and prints the migration command, so you do not
have to find it here. For reference, it is:

```bash
export AIOS_STATE_DIR=/your/configured/dir      # or the --state-dir you pass

cp -a ~/.aios/state.json ~/.aios/stacks.json ~/.aios/models.db \
      ~/.aios/config.json ~/.aios/api_keys.json ~/.aios/tenants.json \
      ~/.aios/metering.json ~/.aios/metering_log.jsonl \
      ~/.aios/lora_registry.json ~/.aios/trust_baseline.json \
      ~/.aios/warmup_schedule.json ~/.aios/hooks_subscriptions.json \
      ~/.aios/recovery_policy.json ~/.aios/guard_stats.json \
      "$AIOS_STATE_DIR"/ 2>/dev/null

cp -an ~/.aios/audit ~/.aios/logs ~/.aios/plugins "$AIOS_STATE_DIR"/ 2>/dev/null

aictl status        # your node, models and stacks should be visible again
```

Two details that matter, both found by testing this command rather than
trusting it:

- It **overwrites** those specific files in the target, deliberately. An
  earlier version used `cp -n`, which silently migrated nothing: running any
  v1.7.0 command first creates an empty `state.json` and `models.db` in the new
  location, and `-n` then refuses to replace them. Since none of the listed
  files were *ever* written to a configured directory before v1.7.0, anything
  there is that freshly-created empty and safe to replace — so run this soon
  after upgrading, before accumulating new data you would rather keep.
- The second line uses `-n` on purpose. `audit`, `logs` and `plugins` are
  directories that may legitimately hold both old and new content, so it merges
  without clobbering.

Nothing is deleted from `~/.aios`; once `aictl status` looks right, remove it
yourself.

**If you set `fair_share_policy` to `enforce` or `warn`,** its measurement
window changed. It compared all-time cumulative service; it now compares the
last hour. This is the fix for a tenant being throttled today because of load
it generated last month, and for most deployments it is what you wanted. To
keep the previous behaviour exactly:

```bash
aictl config set fair_share_window_seconds 0
```

The gate still falls back to cumulative on its own if the window cannot be
measured completely, and `fair_share_policy` still defaults to `off`, so
deployments that never enabled it are unaffected.

Precedence is now explicit: `--state-dir` beats `AIOS_STATE_DIR`, which beats
the `AICTL_STATE_DIR` alias, which beats `~/.aios`. An empty value means unset
rather than the current directory.
