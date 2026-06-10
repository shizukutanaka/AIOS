"""aictl otel — OpenTelemetry configuration management."""

from __future__ import annotations

from typing import Any

import argparse

from pathlib import Path
from aictl.core.output import ok
from aictl.core.config import load_config


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("otel", help="OpenTelemetry configuration")
    osub = p.add_subparsers(dest="otel_cmd")

    gen = osub.add_parser("config", help="Generate OTel Collector config")
    gen.add_argument("--output", default="", help="Write to file")
    gen.set_defaults(func=run_config)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_config(args: argparse.Namespace) -> int:
    """Execute the config subcommand."""
    from aictl.metrics.collector_config import generate_otel_config
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    config = load_config(state_dir)
    yaml_str = generate_otel_config(config)

    out = getattr(args, "output", "")
    if out:
        try:
            Path(out).write_text(yaml_str)
            ok(f"OTel Collector config written to {out}")
        except OSError as e:
            from aictl.core.output import err as print_err
            print_err(f"Cannot write to {out}: {e}")
            return 1
    else:
        print(yaml_str)
    return 0
