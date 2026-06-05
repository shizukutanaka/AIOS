"""aictl serve — start the local control daemon."""

from __future__ import annotations

from typing import Any

import argparse
from aictl.core.constants import DAEMON_HOST, DAEMON_PORT

from pathlib import Path


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("serve", help="Start aiosd local control daemon")
    p.add_argument("--host", default=DAEMON_HOST)
    p.add_argument("--port", type=int, default=DAEMON_PORT)
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the serve command."""
    from aictl.daemon.aiosd import serve
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    serve(host=args.host, port=args.port, state_dir=state_dir)
    return 0
