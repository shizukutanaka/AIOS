"""aictl node — node discovery, pairing, and cluster management."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, warn, print_json, print_kv, print_table
from aictl.core.state import StateStore
from aictl.runtime.nodes import NodeManager


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("node", help="Node and cluster management")
    nsub = p.add_subparsers(dest="node_cmd")

    token = nsub.add_parser("token", help="Generate a join token")
    token.set_defaults(func=run_token)

    pair = nsub.add_parser("pair", help="Pair with a remote node")
    pair.add_argument("address", help="Remote node IP or hostname")
    pair.add_argument("--token", required=True, help="Join token from remote node")
    pair.set_defaults(func=run_pair)

    ls = nsub.add_parser("list", help="List cluster nodes")
    ls.set_defaults(func=run_list)

    status = nsub.add_parser("status", help="Check cluster status")
    status.set_defaults(func=run_status)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_token(args: argparse.Namespace) -> int:
    """Execute the token subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = NodeManager(store)
    token = mgr.generate_join_token()

    if getattr(args, "json", False):
        print_json({"token": token})
        return 0

    ok("Join token generated")
    print(f"\n  Token: {token}")
    print("\n  On the other node, run:")
    print(f"  aictl node pair <this-node-ip> --token {token}")
    return 0


def run_pair(args: argparse.Namespace) -> int:
    """Execute the pair subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = NodeManager(store)
    success, msg = mgr.pair(args.address, args.token)

    if getattr(args, "json", False):
        print_json({"success": success, "message": msg})
        return 0 if success else 1

    if success:
        ok(msg)
        promote, reason = mgr.should_promote_to_k3s()
        if promote:
            print(f"\n  → {reason}")
            print("  Consider: aictl cluster promote")
    else:
        err(msg)

    return 0 if success else 1


def run_list(args: argparse.Namespace) -> int:
    """Execute the list subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = NodeManager(store)
    cs = mgr.load_cluster()

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json(asdict(cs))
        return 0

    print(f"Mode: {cs.mode}")
    if cs.peers:
        rows = [
            {"node_id": p.node_id[:8], "hostname": p.hostname,
             "address": p.address, "role": p.role, "status": p.status}
            for p in cs.peers
        ]
        print_table(rows, ["node_id", "hostname", "address", "role", "status"])
    else:
        print("No peers. Generate a token: aictl node token")

    return 0


def run_status(args: argparse.Namespace) -> int:
    """Execute the status subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = NodeManager(store)
    peers = mgr.check_peers()
    cs = mgr.load_cluster()

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json({"mode": cs.mode, "peers": [asdict(p) for p in peers]})
        return 0

    active = sum(1 for p in peers if p.status == "active")
    total = len(peers)
    print_kv([
        ("Mode", cs.mode),
        ("Peers", f"{active}/{total} active"),
    ])

    promote, reason = mgr.should_promote_to_k3s()
    if promote:
        warn(reason)

    return 0
