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
    from aictl.cmd import fit, quant, troubleshoot
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

    # Plugins (user-defined extensions)
    try:
        from aictl.core.plugins import register_plugins
        register_plugins(sub)
    except Exception:
        pass  # best-effort; failure is non-critical

    return p


def main() -> int:
    """Main."""
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
    args = parser.parse_args()
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
