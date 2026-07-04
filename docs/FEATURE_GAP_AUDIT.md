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
| ~~P5~~ | ~~Tenant resource caps~~ (`max_gpu_slices` / `max_memory_gb` / `max_vram_gb` / `max_models`) | — | ✅ **CLARIFIED (Pass 168)** — rather than inventing fake local enforcement for hardware-allocation concepts a request-routing proxy can't meaningfully check per-request, `tenant classes` now explicitly marks these as generation-only (`*` + legend in human output, `enforcement.generation_only` list in `--json`), distinct from the fields the proxy actually enforces live (`enforcement.proxy_enforced`). No false promise remains either way. | ~~MED~~ done |
| P6 | **`audit_level`** (minimal/standard/detailed per tenant) | `TenantClass.audit_level` | 0 external references. Audit verbosity is uniform regardless of class. | **LOW** |
| P7 | **Integration hooks** (`on_slo_violation`, `on_stack_applied`, …) | `core/hooks.py`, `aictl hooks` | Emitters exist and are called, but they only write to a log/no-op sink; not wired to run user scripts or webhooks. `aictl hooks` inspects, doesn't dispatch. | **LOW** (partially real) |
| ~~P8~~ | ~~**`aictl gate`** ships without a security check~~ | — | ✅ **FIXED (Pass 171)** — `gate` now runs `core/security.scan()` as a new "Security" step. Deliberately a smoke test (scanner completes all checks without raising, on an isolated tmp state dir), not a score/finding gate — the checks describe host-environment posture (root/rootless, cgroup v2, container runtime), so hard-gating on them would make `gate` flaky across CI/sandbox environments, the same class of problem already fixed for ruff/mypy not-installed. Score is reported as informational detail only (mirrors `doctor --deep`'s convention). | ~~LOW~~ done |

### 🚨 New this pass — worse than paper-only: false success

| # | Capability | Reality | Severity |
|---|---|---|---|
| ~~P9~~ | ~~**Go port `apply`/`down`**~~ | — | ✅ **FIXED (Pass 169)** — both now print no leading success text, emit an honestly-labeled `"status": "not_implemented"` JSON body (with a `delegate` pointing at the Python CLI), and `RunE` returns a non-nil error so cobra exits 1 in every mode. Verified via `gofmt` (no network required — full syntax parse succeeds, zero diff in the touched functions) since `go build`/`go test` remain blocked in this sandbox by the cobra module-checksum failure (not bypassed). | ~~HIGH~~ done |

---

## ➖ Missing — expected but absent (不足)

| # | Gap | Why expected |
|---|---|---|
| ~~M1~~ | ~~No `apikey`↔`tenant` reverse view~~ | ✅ **FIXED (Pass 168)** — `apikey inspect` now shows the linked tenant + class (human and `--json`), reusing `find_tenant_by_key_id`. |
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
3. ✅ **M1 + P5 — DONE (Pass 168).** `apikey inspect` now shows the linked
   tenant; `tenant classes` explicitly marks which fields the proxy actually
   enforces live vs which are generation-only (K8s/cgroup), removing the
   false-promise ambiguity without inventing enforcement the architecture
   can't honestly provide.
4. ✅ **P9 — DONE (Pass 169).** `apply`/`down` fail loudly instead of claiming a
   false success: no leading "✓", `RunE` returns a non-nil error (non-zero
   exit via cobra's default handling), and the message points at the Python
   CLI. Verified via `gofmt` (no network needed) since `go build`/`go test`
   remain blocked in this sandbox.
5. ✅ **P8 — DONE (Pass 171).** `gate` now runs the project's own security
   scanner as a smoke test (not a score gate, to avoid the exact
   environment-dependent flakiness the ruff/mypy not-installed fix already
   solved once). The remaining two paper-only items (P6 `audit_level`, P7
   hooks dispatch) are both LOW severity and left open by deliberate choice.

**All items in this audit are now resolved except two deliberately-deferred
LOW-severity paper-only items (P6, P7).** Each fix was a self-contained pass
in the same style (Passes 164/165/167/168/169/171): wire an existing,
documented-but-inert capability to a real runtime consumer, with regression
tests. A future audit pass should re-run the same methodology (grep every
capability field for a real call site) to catch any new gaps introduced
since.

### Self-audit finding (Pass 170) — bug introduced earlier in this same session

Applying the audit's own V1-V7 discipline to the scheduler feature this
session added in Pass 167 (not pre-existing code) turned up one gap: `aictl
warmup schedule --every` accepted a non-positive interval with no validation
(unlike its sibling `--top` flag, which already rejected `< 1`). A
non-positive `interval_secs` persisted into `warmup_schedule.json` would make
`aictl.core.scheduler.run_due_warmup`'s `next_run = now + interval_secs`
compute a timestamp `<= now`, causing the warmup to busy-fire on every
scheduler tick (every 60s) forever instead of respecting `--every`. Fixed
with the session's standard dual guard: CLI-level rejection in
`aictl/cmd/warmup.py`'s `run_schedule` (new `MIN_SCHEDULE_INTERVAL_SECS`
constant), plus a floor at the `run_due_warmup` library chokepoint so a
hand-edited or legacy schedule file can't reproduce the same failure by
bypassing the CLI. See `tests/test_new_features_170.py`.
