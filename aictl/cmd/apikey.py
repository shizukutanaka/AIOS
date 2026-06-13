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

    inspect = ksub.add_parser("inspect", help="Show full metadata for an API key")
    inspect.add_argument("key_id", help="Key ID to inspect")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=run_inspect)

    rotate = ksub.add_parser("rotate", help="Rotate an API key (revoke + create replacement)")
    rotate.add_argument("key_id", help="Key ID to rotate")
    rotate.set_defaults(func=run_rotate)

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


def run_inspect(args: argparse.Namespace) -> int:
    """Show full metadata for an API key."""
    import time as _time
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    mgr = KeyManager(state_dir)
    keys = mgr.list_keys()
    match = next((k for k in keys if k["key_id"] == args.key_id or
                  k["key_id"].startswith(args.key_id)), None)

    if match is None:
        err(f"Key not found: {args.key_id}")
        if getattr(args, "json", False):
            print_json({"found": False, "key_id": args.key_id})
        return 1

    if getattr(args, "json", False):
        print_json(match)
        return 0

    def _fmt_ts(ts: float) -> str:
        return _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(ts)) if ts else "—"

    print(f"API Key: {match['name']}")
    print(f"  id          : {match['key_id']}")
    print(f"  active      : {match.get('active', False)}")
    print(f"  rpm limit   : {match.get('rate_limit_rpm', 0)}")
    print(f"  requests    : {match.get('total_requests', 0)}")
    print(f"  tokens      : {match.get('total_tokens', 0)}")
    print(f"  created     : {_fmt_ts(match.get('created_at', 0))}")
    expires = match.get("expires_at", 0)
    print(f"  expires     : {_fmt_ts(expires) if expires else 'never'}")
    return 0


def run_rotate(args: argparse.Namespace) -> int:
    """Rotate an API key: revoke existing, create replacement with same settings."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    mgr = KeyManager(state_dir)
    keys = mgr.list_keys()
    match = next((k for k in keys if k["key_id"] == args.key_id or
                  k["key_id"].startswith(args.key_id)), None)

    if match is None:
        err(f"Key not found: {args.key_id}")
        return 1

    # Revoke old key
    mgr.revoke(match["key_id"])

    # Create replacement with same settings
    import math
    expires_days = 0
    if match.get("expires_at", 0) > 0:
        import time as _time
        remaining = match["expires_at"] - _time.time()
        expires_days = max(1, math.ceil(remaining / 86400))

    raw_key, new_key = mgr.generate_key(
        name=match["name"],
        rate_limit_rpm=match.get("rate_limit_rpm", 60),
        expires_days=expires_days,
    )

    if getattr(args, "json", False):
        print_json({"rotated": True, "old_key_id": match["key_id"],
                     "new_key_id": new_key.key_id, "new_key": raw_key})
        return 0

    ok(f"API key rotated: {match['name']}")
    print(f"\n  Old key revoked: {match['key_id']}")
    print(f"  New key ID:      {new_key.key_id}")
    print(f"  New key:         {raw_key}")
    print("\n  Save this key — it cannot be displayed again.")
    return 0
