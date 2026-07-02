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
4. Tenant-class require_signed_models flag AND global trust_policy=enforce.
   File: proxy.py _proxy_completion calls _model_trust_ok before routing.
   Strictest-wins: tenant requirement overrides a looser global policy.
   Unsigned/unregistered models are blocked when strict; default config
   (trust_policy=warn, no tenant requiring signing) blocks nothing.
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

## STATUS = PAPER-ONLY (documented/configurable, ZERO runtime consumer found)

Severity: HIGH = a security/compliance control that silently does nothing.
MED = feature looks automatic, is actually manual/inert. LOW = cosmetic gap.

14. [MED] "aictl batch add --schedule '0 2 * * *'" persists a scheduled job
    record to batch.json. Nothing in the codebase ever executes a job on
    schedule. There is no background scheduler/worker process. Only
    "aictl batch run <job>" (manual, on-demand) actually runs anything.
15. [MED] "aictl warmup schedule --every 1h" persists a next_run timestamp.
    Nothing in the codebase ever fires a warmup on that schedule.
16. [MED] Tenant-class resource caps max_gpu_slices, max_memory_gb,
    max_vram_gb, max_models (core/tenant.py TenantClass fields). Zero
    references outside the dataclass and the K8s-namespace/cgroup YAML
    generators (generate_k8s_namespace, generate_cgroup_limits). Only take
    effect if the user manually applies that generated YAML to a real
    cluster/systemd; nothing enforces them in local/proxy mode.
17. [LOW] Tenant-class audit_level field (minimal/standard/detailed). Zero
    references outside its own dataclass. Audit verbosity is uniform
    regardless of tenant class.
18. [LOW, partially real] Integration hook emitters in core/hooks.py
    (on_slo_violation, on_stack_applied, on_model_verified, etc.) ARE called
    from their respective code paths, but they only write to a log/no-op
    sink. They do not run user scripts or call webhooks. "aictl hooks"
    inspects configuration; it does not dispatch anything.
19. [LOW] "aictl gate" (the project's own pre-ship quality check: compile,
    import, version, tests, demo, docs, mcp tool count, ruff, mypy) never
    calls core/security.py scan(). The project's own security scanner exists
    with scored findings but is not part of its own "safe to ship" bar.
    ("aictl doctor --deep" does call it separately, so this is a
    completeness gap in gate specifically, not a total absence of the
    scanner.)

## STATUS = FALSE-SUCCESS (worse than paper-only: reports success, does nothing)

20. [HIGH] Go port "aictl apply -f <file>" (go-port/cmd/aictl/main.go,
    function cmdApply): prints "Applying stack from <file>" with a leading
    success checkmark, returns exit code 0, and does not apply anything.
    Source has a literal comment "// TODO: port from Python
    aictl/cmd/apply.py". In --json mode it does include a "status":"stub"
    field, but a caller that only checks the exit code (0 = success) would
    never notice.
21. [HIGH] Go port "aictl down <stack>" (go-port/cmd/aictl/main.go, function
    cmdDown): same pattern — prints a success message, returns exit 0, stops
    nothing. Comment: "// TODO: port from Python".
    Contrast: "aictl deploy plan" in the SAME Go file IS also an
    unimplemented stub, but it is done HONESTLY — it prints a note before
    any other output telling the user to run the Python CLI instead
    ("delegate": "python3 -m aictl deploy plan <model>"). apply/down do not
    do this; they look identical to a real success.
    STATUS OF THE FIX: identified, not yet applied. Verifying a Go source
    change by compiling requires "go build", which fails in the current
    sandboxed session with a module checksum mismatch for
    github.com/spf13/cobra (downloaded bytes don't match go.sum) — a
    security-relevant failure (same class as a failed TLS check) that must
    not be bypassed. The fix itself (stop printing a leading success
    checkmark; print an honest "not implemented in the Go port, use
    python3 -m aictl apply/down instead" message; return a non-zero exit
    code) is straightforward and low-risk, but needs to be applied and
    compiled in an environment with working Go module access.

## STATUS = MISSING (expected capability, does not exist)

22. No reverse lookup: "aictl apikey inspect <id>" does not show which
    tenant (if any) that key is linked to, even though
    "aictl tenant link-key" (added this session) creates that link.
23. No proxy-level enforcement point existed for model trust before this
    session; item 4 above is the fix. (Listed here for history; this gap is
    now CLOSED.)
24. No single background scheduler/worker process in the daemon. This is the
    prerequisite for making items 14 and 15 real instead of paper-only.

## STATUS = EXCESS (reviewed for redundancy, no action taken)

25. Monitoring commands status / watch / dash / top / health: five commands
    that could look redundant. Reviewed individually; each has a genuinely
    distinct purpose (one-shot unified status snapshot / continuous refresh
    loop / one-screen aggregate dashboard / htop-style GPU+model monitor /
    pass-fail scored health check). Conclusion: not redundant, no merge
    recommended.
26. "aictl deploy" has 9 subcommands (plan, manifest, dynamo, kvbm, disagg,
    optimize, strategy, modelservice, dry-run). Reviewed: "deploy strategy"
    is a front-door recommender that tells the user which of the other 8 to
    run next; the rest each produce a genuinely different output format
    (Helm values, K8s manifests, vLLM flags, Dynamo config). Conclusion: not
    redundant, no merge recommended.

## Recommended next action, in priority order

1. Item 20/21 (Go port apply/down false success) — highest priority. Fix is
   simple (stop claiming success, exit non-zero, point at the Python CLI)
   but requires an environment where "go build ./..." succeeds to verify.
2. Items 14+15+24 together — one background scheduler/worker that executes
   persisted batch jobs and warmup schedules on their recorded timing.
3. Item 16 — either enforce tenant resource caps locally, or update the CLI
   help text to state they are K8s-only (generated-manifest) limits, to stop
   implying local enforcement that does not exist.
4. Item 22 — small, cheap addition (show tenant on "apikey inspect").
