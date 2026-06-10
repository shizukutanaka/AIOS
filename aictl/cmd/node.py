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

    cordon = nsub.add_parser("cordon", help="Mark node as unschedulable")
    cordon.add_argument("node_id", help="Node ID or hostname")
    cordon.add_argument("--json", action="store_true", help="JSON output")
    cordon.set_defaults(func=run_cordon)

    uncordon = nsub.add_parser("uncordon", help="Mark node as schedulable again")
    uncordon.add_argument("node_id", help="Node ID or hostname")
    uncordon.add_argument("--json", action="store_true", help="JSON output")
    uncordon.set_defaults(func=run_uncordon)

    drain = nsub.add_parser("drain", help="Cordon node and evict all running models")
    drain.add_argument("node_id", help="Node ID or hostname")
    drain.add_argument("--force", action="store_true", help="Skip eviction confirmation")
    drain.add_argument("--json", action="store_true", help="JSON output")
    drain.set_defaults(func=run_drain)

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


def _find_peer(mgr: "NodeManager", node_id: str) -> "Any | None":
    """Find a peer by node_id prefix or hostname."""
    cs = mgr.load_cluster()
    for p in cs.peers:
        if p.node_id.startswith(node_id) or p.hostname == node_id:
            return p
    return None


def run_cordon(args: argparse.Namespace) -> int:
    """Mark a node as unschedulable (cordoned)."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = NodeManager(store)
    peer = _find_peer(mgr, args.node_id)
    if not peer:
        err(f"Node '{args.node_id}' not found")
        if getattr(args, "json", False):
            print_json({"success": False, "error": f"node '{args.node_id}' not found"})
        return 1

    peer.status = "cordoned"
    cs = mgr.load_cluster()
    for i, p in enumerate(cs.peers):
        if p.node_id == peer.node_id:
            cs.peers[i] = peer
    mgr.save_cluster(cs)

    if getattr(args, "json", False):
        print_json({"success": True, "node_id": peer.node_id, "status": "cordoned"})
        return 0
    ok(f"Node '{peer.hostname or peer.node_id[:8]}' cordoned — no new workloads will be scheduled")
    return 0


def run_uncordon(args: argparse.Namespace) -> int:
    """Mark a node as schedulable again."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = NodeManager(store)
    peer = _find_peer(mgr, args.node_id)
    if not peer:
        err(f"Node '{args.node_id}' not found")
        if getattr(args, "json", False):
            print_json({"success": False, "error": f"node '{args.node_id}' not found"})
        return 1

    peer.status = "active"
    cs = mgr.load_cluster()
    for i, p in enumerate(cs.peers):
        if p.node_id == peer.node_id:
            cs.peers[i] = peer
    mgr.save_cluster(cs)

    if getattr(args, "json", False):
        print_json({"success": True, "node_id": peer.node_id, "status": "active"})
        return 0
    ok(f"Node '{peer.hostname or peer.node_id[:8]}' uncordoned — ready to accept workloads")
    return 0


def run_drain(args: argparse.Namespace) -> int:
    """Cordon a node and evict all running models/services."""
    store = StateStore(getattr(args, "state_dir", None))
    mgr = NodeManager(store)
    peer = _find_peer(mgr, args.node_id)
    if not peer:
        err(f"Node '{args.node_id}' not found")
        if getattr(args, "json", False):
            print_json({"success": False, "error": f"node '{args.node_id}' not found"})
        return 1

    # Step 1: cordon
    peer.status = "draining"
    cs = mgr.load_cluster()
    for i, p in enumerate(cs.peers):
        if p.node_id == peer.node_id:
            cs.peers[i] = peer
    mgr.save_cluster(cs)
    ok(f"Node '{peer.hostname or peer.node_id[:8]}' cordoned (draining)...")

    # Step 2: evict services on this node (best-effort via stop_stack for local node)
    evicted: list[str] = []
    if peer.node_id == store.load_node().node_id:
        from aictl.stack.orchestrator import list_running, stop_stack
        services = list_running()
        for svc in services:
            stack = svc.get("stack", svc.get("name", ""))
            if stack:
                stopped = stop_stack(stack)
                evicted.extend(stopped)

    peer.status = "cordoned"
    for i, p in enumerate(cs.peers):
        if p.node_id == peer.node_id:
            cs.peers[i] = peer
    mgr.save_cluster(cs)

    if getattr(args, "json", False):
        print_json({"success": True, "node_id": peer.node_id,
                    "status": "cordoned", "evicted": evicted})
        return 0

    ok(f"Drain complete — {len(evicted)} service(s) evicted")
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
