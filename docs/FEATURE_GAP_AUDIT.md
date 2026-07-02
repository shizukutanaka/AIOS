# Feature 過不足 Audit (excess / deficiency selection)

Systematic classification of aictl features by **whether the documented capability
is actually enforced at runtime**, produced by grepping every "capability" field /
module for a real consumer outside its own definition and test files.

Three buckets:
- **✅ Real** — the feature does what it says, verified by a runtime call site.
- **📄 Paper-only (不足 / deficiency)** — documented as a capability, but no code
  path ever consumes it. Cosmetic until wired.
- **➖ Missing (不足)** — a natural, expected capability that simply isn't there.

Excess (過剰) is called out separately at the end; the codebase has very little.

---

## ✅ Real — verified enforced

| Feature | Evidence |
|---|---|
| Per-API-key rate limit (rpm/tpm) | `proxy._check_auth` → `KeyManager.check_rate_limit` |
| Tenant-class rpm/tpm limit | `proxy._check_auth`/`_meter_tokens` → `TenantRateLimiter` (Pass 164) |
| Tenant-class `allow_internet` | `proxy._try_cloud_fallback` → `_tenant_disallows_internet` (Pass 165) |
| API-key file confidentiality (0600) | `apikeys._save_keys` atomic + mode=0o600 (Pass 157) |
| Trust digest verify (format-insensitive) | `trust.verify.verify_digest` (Pass 147) |
| Trust baseline / drift detection | `trust.baseline.BaselineStore.check_all` (Pass 148/162) |
| Cosign keyless identity-pin warning | `trust.cosign.verify_image` warning (Pass 160) |
| Cloud-fallback config (set/redact/export) | `Config.fallback` + `config show` redaction (Pass 159) |
| Persisted-state integrity (V6/V7) | dict-root guards + atomic writes across all loaders (Pass 151–158) |
| SLO governor auto-scale | `daemon/governor.py` background loop |
| Doctor `--fix` incl. trust drift | `build_remediations` + `_trust_baseline_remediations` (Pass 163) |

---

## 📄 Paper-only — documented capability, NO runtime consumer (不足)

Ranked by severity (impact if a user relied on the documented behavior).

| # | Capability | Where it's promised | Reality | Severity |
|---|---|---|---|---|
| ~~P1~~ | ~~**`trust_policy: enforce`** blocks unsigned model loads~~ | — | ✅ **FIXED (Pass 166)** — `proxy._model_trust_ok` now blocks unsigned/unknown models at request time when `trust_policy=enforce`. | ~~HIGH~~ done |
| ~~P2~~ | ~~**`require_signed_models`** (tenant class)~~ | — | ✅ **FIXED (Pass 166)** — a regulated tenant (require_signed_models=True) blocks unsigned models even under a loose global policy (strictest wins). | ~~HIGH~~ done |
| ~~P3~~ | ~~**`batch` job scheduling**~~ | — | ✅ **FIXED (Pass 167)** — `aictl.core.scheduler.run_due_batch_jobs` + `SchedulerDaemon` background thread in `aictl serve` (60s interval) actually execute due jobs; `aictl scheduler tick` triggers manually. | ~~MED~~ done |
| ~~P4~~ | ~~**`warmup schedule`**~~ | — | ✅ **FIXED (Pass 167)** — same scheduler daemon fires the persisted warmup schedule when `next_run` passes. | ~~MED~~ done |
| P5 | **Tenant resource caps** (`max_gpu_slices` / `max_memory_gb` / `max_vram_gb` / `max_models`) | `TenantClass` fields | 0 external references. Only materialize into generated K8s YAML — never enforced in local/proxy mode. | **MED** (K8s path OK; local path unguarded) |
| P6 | **`audit_level`** (minimal/standard/detailed per tenant) | `TenantClass.audit_level` | 0 external references. Audit verbosity is uniform regardless of class. | **LOW** |
| P7 | **Integration hooks** (`on_slo_violation`, `on_stack_applied`, …) | `core/hooks.py`, `aictl hooks` | Emitters exist and are called, but they only write to a log/no-op sink; not wired to run user scripts or webhooks. `aictl hooks` inspects, doesn't dispatch. | **LOW** (partially real) |
| P8 | **`aictl gate`** ships without a security check | gate.py checks compile/import/version/tests/demo/docs/mcp/ruff/mypy | `core/security.scan()` exists with scored findings but `gate` never calls it — the project's own "safe to ship" bar doesn't include its own security scanner. | **LOW** |

### 🚨 New this pass — worse than paper-only: false success

| # | Capability | Reality | Severity |
|---|---|---|---|
| P9 | **Go port `apply`/`down`** (`go-port/cmd/aictl/main.go`) | Both print a bare `✓ Applying stack: ...` / `✓ Stopping stack: ...`, return **exit 0**, and do **nothing** — no manifest is applied, no stack is stopped. `apply`'s `--json` mode does include `"status": "stub"`, easy to miss if a caller only checks the exit code. Unlike `deploy plan` (also a stub, but *honestly* labeled with a "use the Python CLI" note printed before any output), these two actively **report false success** for what looks like a real infrastructure operation. A CI job or script driving the Go binary alone (e.g. a minimal container without the Python runtime) would believe a stack was deployed/stopped when it was not. | **HIGH** (false success > missing feature) |

---

## ➖ Missing — expected but absent (不足)

| # | Gap | Why expected |
|---|---|---|
| M1 | No `apikey`↔`tenant` reverse view | `tenant link-key` exists (Pass 164) but `apikey inspect` doesn't show which tenant a key belongs to. |
| ~~M2~~ | ~~No proxy model-level trust hook~~ | ✅ **FIXED (Pass 166)** — `proxy._model_trust_ok` is the interception point, called in `_proxy_completion` before routing. |
| ~~M3~~ | ~~No scheduler/worker daemon surface~~ | ✅ **FIXED (Pass 167)** — `aictl.daemon.scheduler_daemon.SchedulerDaemon`, wired into `aictl serve` the same way GovernorDaemon already is; also exposed manually as `aictl scheduler tick`. |

---

## 過剰 (excess) — assessed, minimal

- **Monitoring commands** (`status`/`watch`/`dash`/`top`/`health`): overlapping at a
  glance but genuinely distinct intents (snapshot / refresh-loop / one-screen /
  GPU-htop / pass-fail scoring). **Not redundant** — no consolidation warranted.
- **`deploy` subcommands** (9): `deploy strategy` is the front-door recommender for
  the other 8; the rest are distinct output formats (Helm / disagg / KServe / …).
  **Not redundant.**

---

## Recommended next order of work

1. ✅ **P1 + P2 + M2 — DONE (Pass 166).** The proxy now has a model-trust gate
   (`_model_trust_ok`); `trust_policy: enforce` and tenant
   `require_signed_models` actually block unsigned/unknown models at request
   time. Two compliance controls turned from decorative into real.
2. ✅ **P3 + P4 + M3 — DONE (Pass 167).** New `aictl.core.scheduler` module +
   `SchedulerDaemon` background thread (wired into `aictl serve`, 60s interval)
   + manual `aictl scheduler tick` trigger. Both "looks automatic, is manual"
   gaps closed. Side discovery while building this: `cmd/batch.py`'s
   `_db_path()` ignored `--state-dir` entirely (only read AIOS_STATE_DIR) —
   fixed alongside, since it directly blocked testing the scheduler.
3. **P9 — highest priority remaining.** Go port `apply`/`down` must stop
   reporting success for a no-op: either implement them for real, or fail loudly
   (non-zero exit, clear stderr message, no leading "✓") until they are. False
   success is worse than a missing feature — it's actively misleading. Blocked
   in THIS session by a `go build` module-checksum failure in the sandbox (see
   docs/FEATURE_GAP_LIST.md item 21) — needs an environment with working Go
   module access to compile-verify.
4. **P5** — enforce local-mode tenant resource caps (or clearly document them as
   K8s-only to avoid a false promise).

Each is a self-contained pass in the same style as 164/165/167 (wire an existing,
documented-but-inert capability to a real runtime consumer, with regression tests).
