# aictl — Specification

> Normative specification of `aictl` behavior and contracts, written to be
> *checkable*: the global invariants below are enforced by
> `tests/test_spec_conformance.py`. Where the implementation diverged from this
> spec, the gap is recorded in §7 with its resolution. Version 1.6.0.

## 1. Purpose & scope

`aictl` is a single CLI for operating local AI inference infrastructure on
immutable Linux: detecting engines, recommending/quantizing/serving models,
routing requests, RAG, guardrails, observability, and exporting to Kubernetes.
It is implemented in Python 3.11+ (stdlib only) with a Go port of a command
subset. This document specifies the contracts every part must uphold; it is not
a tutorial (see `docs/QUICKSTART.md`) nor an architecture log (see ADRs).

## 2. Global invariants (normative)

These hold for the whole CLI and are machine-checked.

- **G1 — JSON output.** Every command that emits *computed data* must support
  machine-readable output via the global flag `aictl --json <command> …`
  (commands read `args.json`) and/or a local `--json` on the (sub)command.
  **Exempt** are commands that produce no computed data:
  - interactive/TUI: `chat`, `dash`, `watch`, `demo`
  - long-running servers: `serve`, `proxy`
  - guided/action with no data payload: `setup`, `completion`, `update`
  - passthrough to another tool: `logs` (delegates to the container runtime)
  - non-JSON artifact generators: `otel` (emits OTel Collector **YAML**)

  The exempt set is closed: any other command must honor JSON. (Note: `scale`
  emits Kubernetes manifests as JSON already.)
- **G2 — Exit codes.** `0` = success; non-zero = failure. Advisors that find
  "nothing matched" return non-zero (e.g. `spec recommend <unknown>` → 1).
  `main()` normalizes a handler that returns `None` to `0`. Message severity
  must agree with the exit code: a command that exits `0` reports with `ok`/
  `warn`, never `err` (benign no-ops are warnings, not errors).
- **G3 — No external Python dependencies.** Only the 3.11+ standard library may
  be imported by `aictl/**`. (Enforced socially + by `aictl gate` import check.)
- **G4 — Centralized constants.** Ports, versions, and engine metric prefixes
  live in `aictl/core/constants.py`; no hardcoded ports/versions elsewhere.
- **G5 — Command registration.** Every module in `aictl/cmd/*.py` (except
  `__init__`/`__main__`) exposes a `register(subparsers)` callable and is wired
  into `build_parser()` so it appears as a CLI subcommand.
- **G6 — Engine metric prefixes.** vLLM metrics use the `vllm:` prefix; SGLang
  uses `sglang_`. Health checks time out within 5 seconds.

## 3. Command catalog

65 Python commands, grouped by area. All honor **G1** unless marked *exempt*.

| Area | Commands |
|------|----------|
| Lifecycle | `init`, `setup`*, `doctor`, `gate`, `selftest`, `update`*, `upgrade`, `completion`* |
| Inventory/status | `ps`, `status`, `info`, `health`, `report`, `watch`*, `dash`*, `net` |
| Models | `model`, `recommend`, `fit`, `quant`, `convert`, `image`, `warmup` |
| Serving | `serve`*, `proxy`*, `deploy`, `apply`, `down`, `scale`, `recipe`, `chat`* |
| Inference quality | `route`, `spec`, `rag`, `guard`, `cache`, `eval`, `prompt`, `bench`, `perf` |
| Multi-tenant/cost | `tenant`, `quota`, `meter`, `cost`, `tco`, `batch` |
| Cluster/hardware | `cluster`, `node`, `mig`, `lora`, `fabric`, `context` |
| Observability | `otel`*, `trace`, `log`, `logs`*, `audit`, `snapshot`, `diff` |
| Security/ops | `apikey`, `security`, `config`, `troubleshoot`, `demo`* |

`*` = exempt from G1 (see §2). The Go port (`go-port/`) implements a 29-command
subset of the above; parity with Python is intentionally partial, not a gap.

## 4. Daemon REST API (`aiosd`)

`aictl serve` starts `aiosd`, exposing 29 distinct `/v1/*` paths across GET/POST
(health, models, recommend, broker/governor, recipes, stacks, cluster/node,
metering, audit, apikeys, metrics/slo+psi, dynamo, fabric, context, upgrade).
Health is `GET /v1/health`. The OpenAI-compatible proxy (`aictl proxy`) exposes
`/health` and `/v1/*`.

## 5. MCP server

`aictl mcp_server` speaks JSON-RPC 2.0 over stdio and registers 18 tools
mapping to core advisors (recommend, fit, quant, spec, route, rag, guard, …).
Tool inputs/outputs are JSON; tool descriptions are audited for poisoning
(`core/guard.audit_tools`).

## 6. Kubernetes exports

Seven export formats are supported: KServe `InferenceService`, K8s Gateway API
`InferencePool`, KEDA `ScaledObject`, HPA, NVIDIA Dynamo, P/D-disaggregation,
and llm-d `ModelService`. All are emitted as valid JSON/YAML manifests.

## 7. Conformance audit (2026-06)

Auditing the implementation against §2 surfaced two **G1 violations** — commands
that computed data but ignored `--json` entirely:

| Command | Before | Resolution |
|---------|--------|------------|
| `net` | Always printed a text diagnostics table; ignored `--json`. | Added `--json`; refactored probing into `collect_diagnostics()` returning a dict (`engines`/`daemon`/`proxy`/`dns`), printed as JSON or text. |
| `selftest` | Printed unittest's text runner output only. | Added `--json` emitting `{tests_run, failures, errors, skipped, success}`. |

Both are now covered, leaving the exempt set in §2 as the complete list of
non-JSON commands.

A second pass audited **G4** (no hardcoded ports/versions outside
`constants.py`) and found aios's own ports duplicated as literals:

| Location | Before | Resolution |
|----------|--------|------------|
| `runtime/nodes.py` | aiosd port `7700` hardcoded in 4 places (peer default, join payload, join URL, accept default). | Import and use `DAEMON_PORT`. |
| `daemon/proxy.py` | `serve_proxy()` defaulted to literal `8080` / `"127.0.0.1"`. | Default from `PROXY_PORT` / `DAEMON_HOST`. |

The engine ports (`8000`/`11434`/`30000`) that remain as literals in
manifest/quadlet generators are the **engines' own** well-known defaults
(vLLM/Ollama/SGLang) used as fallbacks for `svc.port or <default>`; these are
domain defaults, not aios configuration, and are out of G4 scope.

A third pass audited **G2** (exit codes) and found two issues:

| Location | Before | Resolution |
|----------|--------|------------|
| `__main__.py` | `return rc` passed a handler's `None` straight to `sys.exit`. | Normalize: `rc = rc if isinstance(rc, int) else 0`. |
| `cmd/down.py` | Reported a benign "nothing to stop" no-op via `err(...)` yet returned `0` (severity/exit-code mismatch). | Use `warn(...)`; idempotent teardown stays exit `0`. |

`tests/test_spec_conformance.py` enforces G1 (exempt set is exact — adding a
data command without `--json`, or a stale exemption, fails the suite), G2
(`main` normalizes non-int returns; `down` no-op exits `0`), G4 (aios port
defaults equal the constants; `nodes.py` carries no `7700` literal), and G5
(every `cmd/*` module registers).

### Future candidates (not gaps today)
- `update` could emit a JSON change summary (currently an action command).
- MCP tool calls could emit OTel spans (tracked in `docs/IMPROVEMENTS.md` N).
- Manifest generators could optionally source engine-default ports from the
  `*_DEFAULT_PORT` constants for a single source of truth.
