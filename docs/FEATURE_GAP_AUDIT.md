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
| P3 | **`batch` job scheduling** ("run during GPU idle") | `aictl batch add --schedule '0 2 * * *'` | Jobs are persisted to `batch.json`; **nothing ever executes them**. No scheduler worker in daemon. `batch run` is manual-only. | **MED** — feature looks automatic, is manual |
| P4 | **`warmup schedule`** (recurring preload) | `aictl warmup schedule --every 1h` | Persists `next_run`; no daemon loop fires it. | **MED** |
| P5 | **Tenant resource caps** (`max_gpu_slices` / `max_memory_gb` / `max_vram_gb` / `max_models`) | `TenantClass` fields | 0 external references. Only materialize into generated K8s YAML — never enforced in local/proxy mode. | **MED** (K8s path OK; local path unguarded) |
| P6 | **`audit_level`** (minimal/standard/detailed per tenant) | `TenantClass.audit_level` | 0 external references. Audit verbosity is uniform regardless of class. | **LOW** |
| P7 | **Integration hooks** (`on_slo_violation`, `on_stack_applied`, …) | `core/hooks.py`, `aictl hooks` | Emitters exist and are called, but they only write to a log/no-op sink; not wired to run user scripts or webhooks. `aictl hooks` inspects, doesn't dispatch. | **LOW** (partially real) |

---

## ➖ Missing — expected but absent (不足)

| # | Gap | Why expected |
|---|---|---|
| M1 | No `apikey`↔`tenant` reverse view | `tenant link-key` exists (Pass 164) but `apikey inspect` doesn't show which tenant a key belongs to. |
| ~~M2~~ | ~~No proxy model-level trust hook~~ | ✅ **FIXED (Pass 166)** — `proxy._model_trust_ok` is the interception point, called in `_proxy_completion` before routing. |
| M3 | No scheduler/worker daemon surface | Prereq for P3/P4 — batch + warmup schedules need a single background executor. |

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
2. **P3 + P4 + M3 together** — one background scheduler that executes persisted
   batch jobs and warmup schedules; closes both "looks automatic, is manual" gaps.
3. **P5** — enforce local-mode tenant resource caps (or clearly document them as
   K8s-only to avoid a false promise).

Each is a self-contained pass in the same style as 164/165 (wire an existing,
documented-but-inert capability to a real runtime consumer, with regression tests).
