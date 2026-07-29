# INSTRUCTIONS — Sonnet sessions (scoped implementation / mechanical passes)

> Playbook for improvement sessions driven by a fast, capable model (Claude
> Sonnet class) on tasks whose design is ALREADY decided. If a task needs a
> design decision, research, or an architecture change, STOP and defer it to
> an Opus session (`INSTRUCTIONS_OPUS.md`) — do not design on the fly here.
>
> `CLAUDE.md` rules always apply on top of this document.

## 1. What Sonnet passes are for

Take tasks from `docs/REVIEW_v1.7.0.md` proposals marked **Sonnet**, or any
IMPROVEMENTS.md item whose approach is fully specified. Good fits:

- Model catalog / advisor data refreshes (`runtime/recommend.py`,
  `cmd/quant.py`, `runtime/speculative.py`) — add rows, keep formats.
- Constant additions (`core/constants.py` — no hardcoded ports/versions
  anywhere else).
- Version bumps and the test-pin updates they require.
- Documentation sync (README / CHANGELOG / docs) with the code.
- rolling-window fairness counter in `core/metering.py` +
  `core/fairness.py` (design already sketched in Pass 190 comments).
- CI YAML upkeep once an Opus session has designed the workflow.

## 2. Do-NOT list (defer to Opus)

- Anything introducing a new subsystem, data model, or per-tenant/live
  control path (fair-share scheduler, guard session state, MCP persistence).
- Choosing an unverified external wire format / API contract.
- `git sed -i 's/OLD/NEW/g'` across the tree for a version bump — it will
  corrupt test-data strings. Version literals live in ~20 files; some are
  genuine current-version assertions (bump them) and some are arbitrary
  fixture data like a snapshot's `"version": "1.6.0"` (leave them). Inspect
  each occurrence's context before editing. (This exact trap bit the v1.7.0
  bump — see `docs/REVIEW_v1.7.0.md` weakness #1.)

## 3. Copy-paste checklists

### Add a config field
1. `core/config.py`: dataclass field with a comment stating default = off/safe.
2. `core/config.py` `load_config()`: `c.FIELD = data.get("FIELD", c.FIELD)`.
3. `cmd/config.py` `_dict_to_config`: `FIELD=d.get("FIELD", <default>)`.
4. `cmd/config.py` `_validate_config`: a bounds/scheme check block.
5. If the field is an endpoint, add an http/https scheme check in BOTH
   `_validate_config` and the library chokepoint (defense in depth).

### Add a CLI flag
- Read it with `getattr(args, "flag", <default>)`, never `args.flag`
  (pre-existing tests construct `argparse.Namespace` without your attr).
- All command output must support `--json`.
- Register new commands in `__main__.py` (import + `register(sub)`).

### Engine-metric gotchas
- vLLM metrics use the `vllm:` prefix (colon); SGLang uses `sglang_`
  (underscore). Opt-in engines (LMDeploy / TensorRT-LLM / LM Studio) have no
  Prometheus contract — `scrape_metrics()` returns basic status, don't guess
  metric names. Cosign v3 requires `--output json`.

## 4. The Pass procedure (identical to Opus — no shortcuts)

1. Implement per the checklist above; keep new behavior off by default.
2. New tests in `tests/test_new_features_<n>.py` (default-off no-op,
   validation bounds, CLI wiring — same coverage bar as Opus).
3. `python3 -m unittest discover -s tests` green **twice**.
4. `python3 -m aictl gate` GREEN **twice**.
5. Update `CLAUDE.md` counts (3 places) + `docs/IMPROVEMENTS.md` status.
6. One commit, detailed message, **no model identifier**.
7. `git fetch` → verify fast-forward → `git push -u origin <branch>`.
   Never force-push. Retry only on network errors; a 403 is policy → report.

## 5. When blocked

If a step hits a permission/policy wall (e.g. tag push 403, GitHub App
missing `contents`/`workflows`, direct api.github.com gateway-blocked),
report the exact error and hand it to the user. Do NOT loop on retries and
do NOT attempt to route around the control — these walls are known and
intentional (see `docs/REVIEW_v1.7.0.md` weakness #7 and
`INSTRUCTIONS_OPUS.md` §4).
