#!/usr/bin/env python3
"""aictl — AI Native Linux OS control CLI.

Covers MVP milestones M0–M3:
  M0: CLI skeleton (init, doctor, ps)
  M1: Single-node initialisation + hardware detection
  M2: Stack / Recipe system
  M3: Runtime Broker (GPU/NPU/CPU detection + profile selection)
"""

from __future__ import annotations

import argparse
import sys

from aictl.core.constants import AICTL_VERSION
VERSION = AICTL_VERSION


def build_parser() -> argparse.ArgumentParser:
    """Build parser. All imports are lazy for fast startup."""
    # Lazy imports — only loaded when build_parser() is called
    # This keeps `aictl --version` fast even with 61 commands
    from aictl.cmd import (
        doctor, init, ps, apply, down, recipe, upgrade, model, serve, node,
        logs, config, status, snapshot, cluster, otel, recommend, bench,
        setup, watch, proxy, warmup, net, mig, audit, apikey, image,
        fabric, context, scale, trace, tenant, cost, security, convert, deploy,
        completion, selftest, demo, chat, health, info, report, meter, lora,
        gate, spec,
    )
    from aictl.cmd import log as log_cmd
    from aictl.cmd import fit, quant, troubleshoot, capacity, trust, scheduler
    from aictl.cmd import perf, rag, guard, cache_cmd, dash, update
    from aictl.cmd import tco, quota, batch, diff
    from aictl.cmd import prompt as prompt_cmd
    from aictl.cmd import route
    p = argparse.ArgumentParser(
        prog="aictl",
        description="AI Native Linux OS — local-first AI infrastructure CLI",
    )
    p.add_argument("--version", action="version", version=f"aictl {VERSION}")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--state-dir", default=None, help="Override state directory")
    sub = p.add_subparsers(dest="command")

    # M0
    init.register(sub)
    doctor.register(sub)
    ps.register(sub)

    # M2
    apply.register(sub)
    down.register(sub)
    recipe.register(sub)

    # M3 (model pull is part of runtime broker)
    model.register(sub)

    # M6
    upgrade.register(sub)

    # Daemon
    serve.register(sub)

    # Cluster
    node.register(sub)
    cluster.register(sub)

    # Observability
    logs.register(sub)

    # Config
    config.register(sub)
    status.register(sub)

    # Snapshots
    snapshot.register(sub)

    # Observability
    otel.register(sub)

    # Utilities
    recommend.register(sub)
    bench.register(sub)
    setup.register(sub)
    watch.register(sub)
    proxy.register(sub)
    warmup.register(sub)
    net.register(sub)

    # GPU / Security / Deploy
    mig.register(sub)
    audit.register(sub)
    apikey.register(sub)
    image.register(sub)

    # Enterprise
    fabric.register(sub)
    context.register(sub)
    scale.register(sub)
    tenant.register(sub)
    trace.register(sub)
    cost.register(sub)
    security.register(sub)
    convert.register(sub)
    deploy.register(sub)
    completion.register(sub)
    selftest.register(sub)
    demo.register(sub)
    chat.register(sub)
    health.register(sub)
    info.register(sub)
    report.register(sub)
    meter.register(sub)
    lora.register(sub)
    gate.register(sub)
    spec.register(sub)
    log_cmd.register(sub)

    # Competitor-gap fills (no other tool offers these)
    fit.register(sub)
    capacity.register(sub)
    trust.register(sub)
    scheduler.register(sub)
    quant.register(sub)
    troubleshoot.register(sub)

    # Auto-instrumented performance summary
    perf.register(sub)

    # Zero-config local RAG
    rag.register(sub)

    # Local guardrails
    guard.register(sub)

    # Semantic cache management
    cache_cmd.register(sub)

    # All-in-one dashboard
    dash.register(sub)

    # Self-update + model catalog refresh
    update.register(sub)

    # True Cost of Ownership
    tco.register(sub)

    # Team-based quota management
    quota.register(sub)

    # Background batch job scheduler
    batch.register(sub)

    # Model A/B output comparison (no competitor has this as CLI)
    diff.register(sub)

    # Prompt management and versioning
    prompt_cmd.register(sub)

    # Complexity-aware smart routing (saves 2-5x on cost)
    route.register(sub)

    # Structured / guided-decoding advisor + local JSON-Schema validator
    from aictl.cmd import guided
    guided.register(sub)

    # LLM regression testing — v1.7.0
    from aictl.cmd import eval as eval_cmd
    eval_cmd.register(sub)

    # User-friendly discovery (Apple-style progressive disclosure)
    from aictl.cmd import help as help_cmd
    help_cmd.register(sub)

    # Plugin management
    from aictl.cmd import plugin as plugin_cmd
    plugin_cmd.register(sub)

    # Export (portable bundle)
    from aictl.cmd import export as export_cmd
    export_cmd.register(sub)

    # Import (restore from bundle)
    from aictl.cmd import import_cmd
    import_cmd.register(sub)

    # Live resource monitor
    from aictl.cmd import top as top_cmd
    top_cmd.register(sub)

    # Event bus query and streaming
    from aictl.cmd import events as events_cmd
    events_cmd.register(sub)

    # Daemon lifecycle management
    from aictl.cmd import daemon as daemon_cmd
    daemon_cmd.register(sub)

    # Engine discovery and health
    from aictl.cmd import engines as engines_cmd
    engines_cmd.register(sub)

    # Performance tuning advisor
    from aictl.cmd import optimize as optimize_cmd
    optimize_cmd.register(sub)

    # Integration hooks inspection
    from aictl.cmd import hooks as hooks_cmd
    hooks_cmd.register(sub)

    # cgroup v2 process isolation for inference workloads
    from aictl.cmd import isolation as isolation_cmd
    isolation_cmd.register(sub)

    # Prometheus SLO alert rules management
    from aictl.cmd import alert as alert_cmd
    alert_cmd.register(sub)

    # Plugins (user-defined extensions)
    try:
        from aictl.core.plugins import register_plugins
        register_plugins(sub)
    except Exception:
        pass  # best-effort; failure is non-critical

    return p


def _harden_stdio() -> None:
    """Make output never crash on its own decorative glyphs (✓/✗/—).

    Under a limited stdout encoding (ASCII / C locale — common in minimal
    containers, cron, and some CI) printing a Unicode glyph raises
    UnicodeEncodeError, killing the command. Switch the error handler to
    'backslashreplace' so glyphs degrade (e.g. \\u2713) instead of crashing;
    on the normal UTF-8 terminal this changes nothing.
    """
    import sys as _sys
    for stream in (_sys.stdout, _sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def main() -> int:
    """Main."""
    _harden_stdio()
    # Fast path: --version without loading 61 command modules
    if len(sys.argv) == 2 and sys.argv[1] in ("--version", "-V"):
        print(f"aictl {VERSION}")
        return 0

    # First-run welcome: if the user runs `aictl` with no args at all,
    # show the welcome screen instead of the wall-of-commands help.
    if len(sys.argv) == 1:
        from aictl.core.welcome import show_welcome
        return show_welcome()

    parser = build_parser()
    # Make the global flags `--json` and `--state-dir` positionally uniform.
    # argparse otherwise rejects them when they trail a subcommand that doesn't
    # redefine them (e.g. `aictl cost forecast --json`, `aictl snapshot list
    # --state-dir DIR` both error), even though the leading forms work — an
    # inconsistent surface for documented global flags. Pull both out of argv
    # before parsing (re-deriving --json, capturing --state-dir's value) so they
    # are accepted in leading, middle, or trailing position; subcommands that DO
    # declare --json still parse cleanly.
    json_requested = "--json" in sys.argv
    state_dir_override = None
    argv_for_parse: list[str] = []
    raw = sys.argv[1:]
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok == "--json":
            i += 1
            continue
        if tok == "--state-dir":
            if i + 1 < len(raw):
                state_dir_override = raw[i + 1]
                i += 2
            else:
                i += 1  # dangling flag; let argparse report it
            continue
        if tok.startswith("--state-dir="):
            state_dir_override = tok.split("=", 1)[1]
            i += 1
            continue
        argv_for_parse.append(tok)
        i += 1
    args = parser.parse_args(argv_for_parse)
    # A subcommand that defines its own `--json` resets the dest to its local
    # default (False) during subparser parsing, and the strip above removes the
    # globals from argparse's view, so set them explicitly from the raw argv.
    if json_requested:
        args.json = True
    if state_dir_override is not None:
        args.state_dir = state_dir_override

    # `--state-dir` is a process-wide setting, so publish it to the environment
    # where the whole process can see it. Roughly a dozen helpers resolve the
    # state directory without an argparse namespace in hand — the perf log, the
    # semantic cache, the RAG index, the prefix-reuse log — and they are reached
    # from call sites with no `args` to thread through. Before this, passing the
    # flag moved `state.json` and left every one of those behind in `~/.aios`,
    # which is the same split the environment variable itself used to cause,
    # one level down. Setting it here also carries the choice into subprocesses,
    # which is what the daemon and the parallel test workers need.
    if getattr(args, "state_dir", None):
        import os as _os

        from aictl.core.state import STATE_DIR_ENV, resolve_state_dir
        _os.environ[STATE_DIR_ENV] = str(resolve_state_dir(args.state_dir))

    # Tell a user whose state was stranded by the resolution fix above. The fix
    # is correct and indistinguishable from data loss if nobody says so: their
    # node config, models, API keys and audit log simply read as empty. Warn to
    # stderr so it never contaminates `--json` output, and never fail on it.
    try:
        from aictl.core.state import split_state_warning
        _warning = split_state_warning()
        if _warning:
            import sys as _sys
            print(f"⚠ {_warning}\n", file=_sys.stderr)
    except Exception:                        # pragma: no cover - never fatal
        pass

    if args.command is None:
        parser.print_help()
        return 0

    # Run the command with user-friendly error handling and perf recording
    from aictl.core.perf import measure
    cmd_name = getattr(args, "command", "unknown") or "unknown"
    try:
        with measure(cmd_name) as perf_ctx:
            rc = args.func(args)
            # G2: a handler that returns None (no explicit return) means success;
            # always hand sys.exit a well-defined int.
            rc = rc if isinstance(rc, int) else 0
            perf_ctx["exit_code"] = rc
            return rc
    except KeyboardInterrupt:
        print("\n  Cancelled.", file=sys.stderr)
        return 130
    except Exception as e:
        from aictl.core.errors import format_for_user
        print(format_for_user(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
