# INSTRUCTIONS — Opus sessions (design / research / large-scope passes)

> Playbook for improvement sessions on this repo driven by a high-capability
> model (Claude Opus class). Scope: items that need genuine design judgment,
> multi-source research, or architecture changes. For well-scoped mechanical
> work, hand off to `INSTRUCTIONS_SONNET.md` instead — do not burn a design
> session on catalog updates.
>
> Everything here was validated over 190+ improvement passes on this repo.
> `CLAUDE.md` rules always apply on top of this document.

## 1. Task selection

Take tasks from `docs/REVIEW_v1.7.0.md` proposals marked **Opus**, or open
items in `docs/IMPROVEMENTS.md`. Current large items, in priority order:

1. **Live fair-share scheduler** (item M remainder) — introduce a per-tenant
   concept into `daemon/governor.py` / `runtime/router.py` for the first
   time. `core/fairness.py` (advisory, Pass 190) is the read-model to build
   on; `core/metering.py`'s `TokenMeter.list_usage()` is the data source.
   Must ship opt-in (config-gated, default off) like every other feature.
2. **G-4 cross-request injection context** — persisted per-session finding
   history for `core/guard.py`. Requires a data-model design first
   (retention, keying, privacy); do NOT bolt state onto the proxy ad hoc.
3. **N-3 MCP session persistence / Tasks extension** — blocked on the MCP
   2026-07-28 final spec; verify the spec is final before starting.
4. **CI design** (then hand YAML upkeep to Sonnet) — precondition: the
   Claude GitHub App needs `workflows` permission (a push of
   `.github/workflows/*` was rejected by GitHub without it — verified).

## 2. Research/design method (validated pattern)

For any substantive design, run a Workflow: **2 research agents in parallel
(external sources + codebase context) → N independent design proposals →
1 synthesis agent instructed to be decisive** (converge on ONE design, not a
menu). Then — non-negotiable:

- **Verify every workflow finding against real code before acting on it.**
  Agents cite line numbers and function names; read them yourself. In this
  repo's history, verification caught a naming-collision hazard
  (`cmd/route.py`'s local `_load_config` vs `core.config.load_config`) that
  no agent flagged.
- **Never ship an unverified wire format.** When vLLM's /rerank field names
  could not be confirmed against primary docs (403s), the shipped design
  used only the TEI contract that WAS verified against its OpenAPI spec, and
  said so in comments/docs. Prefer "one verified contract + documented gap"
  over "two guessed contracts".
- If a source is unreachable, write that down in the commit/doc — do not
  fill gaps with invented precision.

## 3. The Pass procedure (full, in order — no skipping)

1. Implement. Follow existing patterns: config fields via the 4-point set
   (dataclass field w/ comment → `load_config()` line → `_dict_to_config`
   line → `_validate_config` bounds block); CLI args read via
   `getattr(args, "x", <default>)` (old tests build bare Namespaces); new
   feature = off by default, true no-op (zero network/disk I/O until enabled).
2. Write focused regression tests in a new `tests/test_new_features_<n>.py`.
   Include: default-off no-op (assert zero mocked calls), degradation paths,
   a real local `http.server` fixture for network features, config
   validation bounds + save/load round-trip, CLI wiring (flags present where
   intended AND rejected where deliberately not wired).
3. `python3 -m unittest discover -s tests` — must be green **twice**.
4. `python3 -m aictl gate` — must be GREEN **twice** (determinism check).
5. Update `CLAUDE.md` counts (3 locations: header line, tests/ map line,
   gate comment) and `docs/IMPROVEMENTS.md` item status in place (mark done
   with pass number, file refs, and any deliberately-deferred scope as an
   explicit documented gap).
6. Commit: one commit, detailed message (what/why/how verified). **Never**
   include a model identifier in commits/PRs/code.
7. `git fetch origin <branch>` → verify fast-forward (0 behind) → `git push
   -u origin <branch>`. Never force-push. Retry pushes only on network
   errors (2s/4s/8s/16s backoff) — a 403 is policy, report it instead.

## 4. Hard stop points (human approval required)

- Merging to `main`, creating tags/Releases, or anything irreversibly
  public-facing → explicit user GO first.
- Known environment walls (verified in this repo's history, do not re-fight
  them): tag pushes are branch-scope-blocked (403); direct api.github.com is
  gateway-blocked; the GitHub App lacks `contents`/`workflows` for release
  and workflow creation. If hit, report the exact error and hand the step
  to the user rather than looping on retries.
- Never add an external Python dependency. Never bypass TLS or Go module
  checksum verification, even when a build is blocked by them.

## 5. Self-audit discipline

Periodically re-apply the project's audit methodology to code added in
earlier passes ("physician, heal thyself"). Multiple real bugs in this repo
were found by auditing session-added code (e.g. SDK fallback emitting
32-dim vectors vs the 64-dim detector; a cache-status flag missing). Treat
your own recent passes as the highest-yield audit surface.
