"""aictl proxy — start OpenAI-compatible completions proxy."""

from __future__ import annotations

from typing import Any

import argparse
from aictl.core.constants import DAEMON_HOST, PROXY_PORT

from aictl.core.output import ok
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("proxy", help="Start OpenAI-compatible proxy (routes through broker)")
    p.add_argument("--host", default=DAEMON_HOST)
    p.add_argument("--port", type=int, default=PROXY_PORT)
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the proxy command."""
    from aictl.daemon.proxy import serve_proxy
    store = StateStore(getattr(args, "state_dir", None))
    ok(f"Starting completions proxy on http://{args.host}:{args.port}")
    ok("Route: /v1/chat/completions → best available engine via broker")
    serve_proxy(host=args.host, port=args.port, store=store)
    return 0
