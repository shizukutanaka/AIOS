"""aictl apikey — API key management for the completions proxy."""

from __future__ import annotations

from typing import Any

import argparse

from pathlib import Path
from aictl.core.output import ok, err, print_json, print_table
from aictl.core.apikeys import KeyManager


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("apikey", help="API key management")
    ksub = p.add_subparsers(dest="apikey_cmd")

    gen = ksub.add_parser("create", help="Generate a new API key")
    gen.add_argument("name", help="Key label")
    gen.add_argument("--rpm", type=int, default=60, help="Requests per minute limit")
    gen.add_argument("--expires", type=int, default=0, help="Expiry in days (0=never)")
    gen.set_defaults(func=run_create)

    ls = ksub.add_parser("list", help="List API keys")
    ls.set_defaults(func=run_list)

    rev = ksub.add_parser("revoke", help="Revoke an API key")
    rev.add_argument("key_id", help="Key ID to revoke")
    rev.set_defaults(func=run_revoke)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_create(args: argparse.Namespace) -> int:
    """Execute the create subcommand."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    mgr = KeyManager(state_dir)
    raw_key, key = mgr.generate_key(
        name=args.name,
        rate_limit_rpm=getattr(args, "rpm", 60),
        expires_days=getattr(args, "expires", 0),
    )

    if getattr(args, "json", False):
        print_json({"key": raw_key, "key_id": key.key_id, "name": key.name})
        return 0

    ok(f"API key created: {key.name}")
    print(f"\n  Key: {raw_key}")
    print(f"  ID:  {key.key_id}")
    print(f"  RPM: {key.rate_limit_rpm}")
    print("\n  Save this key — it cannot be displayed again.")
    return 0


def run_list(args: argparse.Namespace) -> int:
    """Execute the list subcommand."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    mgr = KeyManager(state_dir)
    keys = mgr.list_keys()

    if getattr(args, "json", False):
        print_json(keys)
        return 0

    if not keys:
        print("No API keys. Create one: aictl apikey create <n>")
        return 0

    rows = [{"id": k["key_id"], "name": k["name"],
             "active": "\u2713" if k["active"] else "\u2717",
             "rpm": k["rate_limit_rpm"],
             "requests": k["total_requests"]} for k in keys]
    print_table(rows, ["id", "name", "active", "rpm", "requests"])
    return 0


def run_revoke(args: argparse.Namespace) -> int:
    """Execute the revoke subcommand."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    mgr = KeyManager(state_dir)
    if mgr.revoke(args.key_id):
        ok(f"Key {args.key_id} revoked")
        return 0
    err(f"Key not found: {args.key_id}")
    return 1
