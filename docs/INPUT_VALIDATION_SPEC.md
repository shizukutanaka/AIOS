# aictl — Input Validation & Robustness Specification (仕様書)

> Normative specification of `aictl`'s **input-validation and defensive-coding
> contracts**. Companion to `docs/SPECIFICATION.md` (which defines the global
> invariants G1–G5). The **V-invariants** below are enforced by
> `tests/test_input_validation_spec.py`. Version 1.6.0.

## 1. Purpose & scope (目的・範囲)

Every `aictl` command is a leaf that turns *untrusted argument input* into an
action or a computed answer. A recurring failure mode in CLIs is to accept
physically-impossible input and silently produce a **wrong** result rather than
failing honestly — a class of defect that is invisible to mock-based tests
because the mock never exercises the boundary. This document specifies the
boundary contracts every command must uphold, derived from a systematic audit
of the whole `aictl/cmd` + `aictl/core` + `aictl/runtime` surface.

These contracts are **normative**: new commands must satisfy them, and the
conformance test fails the build (`aictl gate`) on a regression.

## 2. V-invariants (normative)

### V1 — Physical quantities are ≥ 1
Any flag that denotes a **count** (`--top`, `-n`, `-k`, `--gpus`, `--requests`,
`--concurrent`, number of test prompts) or a **physical size/window**
(`--context`, `--vram` GB, `--period-days`, `--per-day`/`--per-month` token
quota where 0 = "unlimited") must reject values below its physical minimum with
a non-zero exit and a one-line message. Rationale: a sub-minimum value either is
meaningless or silently corrupts downstream math (e.g. a negative `--context`
produced a *negative* KV-cache estimate, making a model falsely "fit"; a
negative `--period-days` produced a *negative* total cost of ownership).

- Counts and sizes: minimum **1**.
- Quotas with a documented "0 = unlimited" semantic: minimum **0**; negative
  rejected (a negative quota reads as unlimited under `> 0` enforcement — a
  silent security-relevant failure where the operator believes usage is capped).

### V2 — Identifier hygiene is symmetric
An identifier used as a registry key (tenant id, team, adapter name, entity id,
base-model name) must be **stripped** of leading/trailing whitespace and
**rejected** if empty/whitespace-only. The *same* normalization must be applied
on **both** the write path (create/set) and the **read** path
(inspect/delete/list/filter/lookup), so that an entity provisioned as `"team "`
is reachable as `"team"`. Asymmetry here yields an entity invisible to its own
name.

### V3 — No unguarded negative slice
A user-controlled count must never reach a Python slice expression `seq[:n]` or
`seq[-n:]` without a guard. A negative `n` inverts the slice (`seq[:-3]` returns
*all but the last 3*), returning **more** and **wrong** elements than requested —
the opposite of "top N". Guard at the **library chokepoint** (return `[]` for
`n <= 0`) *and* validate at the CLI (V1). Tail-window readers (`recent(n)`,
`read_recent(n)`) return `[]` for `n <= 0` rather than the whole history.

### V4 — User error is never reported as a bug
Input-validation failures must surface as an *input problem*, never as
"Unexpected error … report a bug". Parsers that read a trailing unit char
(`s[-1]`) must catch `IndexError` (empty string) alongside `ValueError`/
`KeyError` and degrade to a default, so a user's empty `--every ""` does not
crash with a GitHub-issue prompt. `core/errors.format_for_user` maps
`ValueError`/`KeyError` (except `UnicodeError`) to "Invalid input", not the
generic fallback.

### V5 — JSON contract under validation (refines G1)
When a command rejects input under `--json`, it must still exit non-zero and
must **not** emit a partial/garbage JSON body on stdout (a consumer piping to
`jq` must see either valid result-JSON with exit 0, or nothing/clean error with
a non-zero exit). The `--json` exit code must agree with the human-path exit
code for the same input (no "always return 0 under --json").

## 3. 長所 (Strengths)

- **Honest failure is now the default.** Across the validated surface, impossible
  input is rejected at the boundary with a clear message and a correct exit
  code — verified against the *real* CLI, not mocks.
- **Defense-in-depth.** Slice/aggregate guards live at the library chokepoint
  *and* the CLI, so an internal caller (e.g. `fit` → `recommend()`) cannot
  trigger the inverted-slice path either.
- **Division-by-zero is well-contained.** Every aggregate-mean path
  (`cost forecast`, `tco forecast`, `health trends`) early-returns on empty
  input; `quant recommend` skips all candidates before the `/ vram_mb` divide
  when `vram_mb == 0`. No reachable `ZeroDivisionError` was found in the audit.
- **Centralized, machine-checked contracts.** Constants (G4), JSON (G1),
  registration (G5), and now input validation (V1–V5) are enforced by
  conformance tests, not convention alone.
- **Zero external dependencies.** All validation uses the stdlib; no schema
  library is pulled in.

## 4. 短所 (Weaknesses / residual risk)

- **V1/V3 are per-command, not type-enforced.** Validation is hand-written in
  each handler rather than expressed once via an argparse type. A brand-new
  command can forget it; only the conformance test's *sampled* coverage catches
  regressions, not an exhaustive proof.
- **Engine-type filters strip but don't validate membership.** `--engine
  " vllm "` is stripped to `vllm`, but a typo like `--engin` is silently treated
  as "no filter" rather than an error.
- **Go port lags.** The Go subset (`go-port/`) does not yet mirror these
  V-invariants, and cannot be built in network-restricted environments
  (no vendored deps), so Go/Python parity is unverified here.
- **Upper bounds are mostly unbounded.** V1 sets a *floor*; few commands cap the
  ceiling (e.g. `--top 1000000` is accepted), relying on downstream `min(…, len)`.

## 5. 改善点 (Improvement points — prioritized)

1. **[implemented this pass]** Add the V-invariants as a machine-checked
   conformance suite so regressions fail `aictl gate`.
2. A shared `argparse` helper (`positive_int`, `nonneg_int`) to make V1 a
   *type*, eliminating per-handler boilerplate and the forget-to-validate risk.
3. Extend identifier hygiene (V2) to engine-type flags with an explicit
   membership check (reject unknown engine names instead of silently ignoring).
4. Mirror the V-invariants into the Go port and add a cross-language parity
   harness once the build is reproducible offline.
5. Consider sensible ceilings for unbounded counts to prevent accidental
   resource blow-ups (e.g. `warmup --top 100000`).

## 6. Conformance

`tests/test_input_validation_spec.py` mechanically verifies:
- V1/V3: a representative set of count/size flags reject `-1` and `0` with a
  non-zero exit (`recommend`, `optimize`, `route test`, `fit`, `lora auto-tune`,
  `tco`, `warmup`, `rag`, `meter quota`).
- V2: entity commands strip and reject empty identifiers, and create→query is
  symmetric under padding.
- V4: `format_for_user(ValueError(...))` / `KeyError` yields "Invalid input"
  and never the "report a bug" fallback.
- V5: a rejected `--json` invocation exits non-zero and prints no JSON body.
