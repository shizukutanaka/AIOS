# aictl feature gap list (plain reference, for LLM hand-off)

Purpose: a flat, unambiguous list of which product capabilities are REAL
(enforced at runtime, verified by a call site) vs PAPER-ONLY (documented or
configurable but never consumed) vs MISSING (expected, absent) vs FALSE-SUCCESS
(reports success while doing nothing) vs EXCESS (redundant, reviewed, not acted
on). Each item is one fact per line. Verified by direct code inspection
(grep for call sites) plus, where noted, real CLI execution — not guesses.

Repo: shizukutanaka/aios, branch claude/deepresearch-ultrathink-improvement-ffeDG.
Full narrative version with rationale: docs/FEATURE_GAP_AUDIT.md (this file is
the condensed, model-to-model version of the same findings).

## STATUS = REAL (verified enforced, has a runtime call site)

1. Per-API-key rate limit (requests/min, tokens/min). File: aictl/daemon/proxy.py
   function _check_auth. Calls aictl/core/apikeys.py KeyManager.check_rate_limit.
2. Tenant-class rate limit (requests/min, tokens/min). File: proxy.py
   _check_auth and _meter_tokens. Calls core/tenant.py TenantRateLimiter.
   Opt-in: only applies if the API key was linked to a tenant via
   "aictl tenant link-key".
3. Tenant-class allow_internet flag. File: proxy.py _try_cloud_fallback calls
   _tenant_disallows_internet. A tenant with allow_internet=False cannot be
   routed to an external cloud API even if cloud fallback is globally enabled.
4. Tenant-class require_signed_models flag AND global trust_policy=enforce
   (model-trust gate). File: proxy.py _proxy_completion calls
   _model_trust_ok before routing. Strictest-wins: tenant requirement
   overrides a looser global policy. Unsigned/unregistered models are
   blocked when strict; default config (trust_policy=warn, no tenant
   requiring signing) blocks nothing.
5. API-key file confidentiality. File: core/apikeys.py _save_keys. Writes with
   mode 0o600 via atomic_write_text, not the default 0o644.
6. Digest verification is case/prefix insensitive (bare hex, uppercase,
   "sha256:" or "SHA256:" prefix all accepted; wrong/truncated digest still
   rejected). File: aictl/trust/verify.py verify_digest.
7. Model integrity baseline and drift detection. File: aictl/trust/baseline.py
   BaselineStore.check_all. Detects changed/missing files against a recorded
   SHA-256 baseline, with no target path required (system-wide scan).
8. Baseline provenance tagging. "aictl model pull --baseline" tags a baseline
   with source="pull:<reference>"; "aictl trust check"/"list" show it.
9. Cosign keyless verification without a pinned identity now emits a clear
   warning (does not silently claim full trust). File: aictl/trust/cosign.py
   verify_image, warning field on VerifyResult.
10. Cloud-fallback provider config actually works end to end: "aictl config set
    fallback.provider/api_key/model/enabled" persists and is read by
    aictl/runtime/fallback.py load_fallback_config. Secret is redacted in
    "config show"/"config diff" bulk output, NOT redacted in "config get
    fallback.api_key" (explicit single-key request) or in "config export"
    (needed for the export/import round-trip); export file is 0o600.
11. Persisted JSON state (config.json, tenants.json, batch.json, prompts.json,
    quota state, metering state, snapshot/context files, eval suite files,
    import bundles) all: (a) validate the parsed root is a dict before use,
    degrading to a safe default on a corrupt/non-object file instead of
    crashing with an unhandled exception, and (b) write atomically
    (temp file + fsync + os.replace), so a crash mid-write cannot corrupt them.
12. SLO governor background auto-scale loop. File: aictl/daemon/governor.py.
13. "aictl doctor --fix" surfaces trust-baseline drift as a remediation item
    (never auto-applies a fix for drift — always points at manual
    investigation, since silently re-baselining a changed file would defeat
    the point of drift detection).
14. Background scheduler for batch jobs and warmup. "aictl batch add
    --schedule '0 2 * * *'" and "aictl warmup schedule --every 1h" are now
    actually executed, not just persisted. File: aictl/core/scheduler.py
    (cron matching + due-check + execution, reusing the same job/warmup
    execution code paths the manual "batch run"/"warmup run" commands use)
    plus aictl/daemon/scheduler_daemon.py (SchedulerDaemon, a background
    thread wired into "aictl serve" the same way the pre-existing SLO
    governor thread is, 60s interval). Manual trigger: "aictl scheduler
    tick". Daemon status: GET /v1/scheduler.
15. Fixed alongside item 14: aictl/cmd/batch.py's _db_path() previously
    ignored the --state-dir CLI flag entirely (it only read the
    AIOS_STATE_DIR environment variable) — every "aictl batch" command
    silently wrote to the default state directory regardless of --state-dir,
    unlike every other command in the project. Now respects --state-dir
    consistently with the rest of the CLI.

## STATUS = PAPER-ONLY (documented/configurable, ZERO runtime consumer found)

Severity: HIGH = a security/compliance control that silently does nothing.
MED = feature looks automatic, is actually manual/inert. LOW = cosmetic gap.

Remaining open: item 17 (audit_level) and item 18 (hooks dispatch), both LOW.

16. [RESOLVED, Pass 168, reclassified not fixed] Tenant-class resource caps
    max_gpu_slices, max_memory_gb, max_vram_gb, max_models (core/tenant.py
    TenantClass fields). Zero references outside the dataclass and the
    K8s-namespace/cgroup YAML generators (generate_k8s_namespace,
    generate_cgroup_limits) — this is architectural, not a bug: hardware
    allocation belongs to a real cluster/systemd, not a request-routing
    proxy, so no fake local enforcement was invented. Instead,
    "aictl tenant classes" now explicitly marks these fields as
    generation-only (asterisk + legend in human output; the
    "enforcement.generation_only" list in --json), distinct from the fields
    the proxy DOES enforce live ("enforcement.proxy_enforced": rpm, tpm,
    allow_internet, require_signed_models). No caller can mistake one for
    the other anymore.
17. [LOW] Tenant-class audit_level field (minimal/standard/detailed). Zero
    references outside its own dataclass. Audit verbosity is uniform
    regardless of tenant class.
18. [LOW, partially real] Integration hook emitters in core/hooks.py
    (on_slo_violation, on_stack_applied, on_model_verified, etc.) ARE called
    from their respective code paths, but they only write to a log/no-op
    sink. They do not run user scripts or call webhooks. "aictl hooks"
    inspects configuration; it does not dispatch anything.
19. [RESOLVED, Pass 171] "aictl gate" now calls core/security.py scan() as a
    new "Security" step. Deliberately a smoke test, not a score gate: the
    scanner's findings (root vs rootless, cgroup v2 availability, container
    runtime presence) describe the *host environment*, not the code being
    shipped, so hard-failing on the live score would make gate exactly as
    flaky as the pre-fix ruff/mypy steps (fails in any rootless-less
    CI/sandbox). Gate instead verifies scan() completes all checks without
    raising (using an isolated tmp state dir, independent of the caller's
    real state) and reports the score/findings as informational detail only
    — mirroring "aictl doctor --deep"'s existing score-is-informational
    convention. A scanner that itself raises now fails the gate.

## STATUS = FALSE-SUCCESS (worse than paper-only: reports success, does nothing)

(none currently open — both items below were fixed in Pass 169.)

20. [RESOLVED, Pass 169] Go port "aictl apply -f <file>" (go-port/cmd/aictl/
    main.go, function cmdApply) used to print "Applying stack from <file>"
    with a leading success checkmark, return exit code 0, and not apply
    anything. Fix: no leading success text; --json mode emits an honestly-
    labeled "status":"not_implemented" body with a "delegate" field pointing
    at "python3 -m aictl apply -f <file>"; RunE returns a non-nil error in
    every mode, so cobra's default error handling (which this file already
    relies on for cmdApply's own "--file/-f required" validation) prints
    "Error: ..." to stderr and main() calls os.Exit(1). Verified via gofmt
    (no network required — confirms the edited file still parses as valid
    Go, zero formatting diff in the touched functions) since go build/go
    test remain blocked in this sandboxed session by a module checksum
    mismatch for github.com/spf13/cobra (a security-relevant failure, same
    class as a failed TLS check, not bypassed).
21. [RESOLVED, Pass 169] Go port "aictl down <stack>" (same file, function
    cmdDown): identical false-success pattern, identical fix (no leading
    checkmark, honestly-labeled JSON status, non-nil error, delegates to
    "python3 -m aictl down <stack>").

## STATUS = MISSING (expected capability, does not exist)

(none currently open — item 22, the apikey<->tenant reverse lookup, was
fixed in Pass 168; see the Resolved section below.)

## STATUS = EXCESS (reviewed for redundancy, no action taken)

23. Monitoring commands status / watch / dash / top / health: five commands
    that could look redundant. Reviewed individually; each has a genuinely
    distinct purpose (one-shot unified status snapshot / continuous refresh
    loop / one-screen aggregate dashboard / htop-style GPU+model monitor /
    pass-fail scored health check). Conclusion: not redundant, no merge
    recommended.
24. "aictl deploy" has 9 subcommands (plan, manifest, dynamo, kvbm, disagg,
    optimize, strategy, modelservice, dry-run). Reviewed: "deploy strategy"
    is a front-door recommender that tells the user which of the other 8 to
    run next; the rest each produce a genuinely different output format
    (Helm values, K8s manifests, vLLM flags, Dynamo config). Conclusion: not
    redundant, no merge recommended.

## Resolved this session (kept for history, no longer open)

- Model-trust gate (was item: proxy has no model-level trust hook) — now
  item 4 above.
- Background scheduler (was item: no scheduler/worker daemon) — now item 14
  above.
- Tenant resource caps false-promise ambiguity — now item 16 above
  (reclassified: architecturally correct to leave unenforced locally, so the
  fix was making the distinction explicit rather than inventing enforcement).
- apikey<->tenant reverse lookup — "aictl apikey inspect <id>" now shows the
  linked tenant + class (was item 22, MISSING section is now empty).
- Go port apply/down false success (items 20/21) — both now fail loudly
  (non-zero exit, honest "not_implemented" status, delegates to the Python
  CLI) instead of claiming a false success. Verified via gofmt (no network
  needed) since go build/go test remain blocked in this sandbox.
- Gate never invoked the security scanner (item 19) — "aictl gate" now runs
  core/security.py scan() as a smoke test (not a score gate, to avoid
  environment-dependent flakiness). See Pass 171 below.

## Self-audit finding (Pass 170)

25. [RESOLVED, Pass 170] "aictl warmup schedule --every" accepted a
    non-positive interval (e.g. "-1h", "0h") with no validation — this bug
    was self-introduced in this session's own Pass 167 scheduler feature,
    not pre-existing code. A non-positive interval_secs persisted into
    warmup_schedule.json made core/scheduler.py's run_due_warmup compute
    next_run <= now forever, busy-firing the warmup on every scheduler tick
    (60s) instead of respecting --every. Fixed with the session's standard
    dual guard: CLI rejection in cmd/warmup.py's run_schedule (mirrors the
    pre-existing "--top < 1" guard) plus a floor at the run_due_warmup
    library chokepoint (new core/constants.py MIN_SCHEDULE_INTERVAL_SECS =
    60) so a hand-edited/legacy schedule file can't reproduce it either.

## Pass 171

26. [RESOLVED, Pass 171] Item 19 fixed: "aictl gate" now has a "Security"
    step calling core/security.py scan(). Deliberately a smoke test (scanner
    completes all checks without raising, using an isolated tmp state dir),
    not a score/finding threshold — the scanner's checks describe host
    environment posture (root vs rootless, cgroup v2, container runtime),
    which would make gate flaky across CI/sandbox environments if hard-gated
    on, the same class of problem already fixed for ruff/mypy not-installed.
    Score/findings are surfaced as informational detail only, matching
    "aictl doctor --deep"'s existing convention.

## Pass 172

27. [RESOLVED, Pass 172] Self-audit of Pass 166's model-trust gate found a
    bypass: _model_trust_ok was wired into _proxy_completion only —
    _proxy_embedding routed straight to the upstream engine with no trust
    check. With trust_policy=enforce (or a regulated tenant requiring
    signed models), an unsigned/unknown model was blocked on
    /v1/chat/completions yet fully reachable via /v1/embeddings, which
    carries the same document content the policy protects. Fixed by calling
    the same gate before routing in _proxy_embedding; a source-level test
    pins that BOTH paths gate before router.route so a refactor can't
    silently reopen either bypass (tests/test_new_features_172.py).

## Recommended next action

Two LOW-severity paper-only items remain open by deliberate choice, not
oversight: item 17 (tenant audit_level has no effect on log verbosity) and
item 18 (integration hooks log/no-op instead of running user
scripts/webhooks). Both are cosmetic/nice-to-have, not silent security or
correctness failures. A future pass should re-run the same methodology (grep
every documented capability field for a real runtime call site outside its
own definition and tests) to catch any new gaps introduced since this audit.
