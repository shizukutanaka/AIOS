"""aictl upgrade — plan and manage OS and model updates."""

from __future__ import annotations

from typing import Any

import argparse

import time

from aictl.core.output import ok, print_json, print_kv
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("upgrade", help="Upgrade planning")
    usub = p.add_subparsers(dest="upgrade_cmd")

    plan = usub.add_parser("plan", help="Generate upgrade plan")
    plan.add_argument("--target-version", default="", help="Target OS version")
    plan.set_defaults(func=run_plan)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_plan(args: argparse.Namespace) -> int:
    """Execute the plan subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    node = store.load_node()
    stacks = store.load_stacks()
    target = args.target_version or "next"

    plan: dict[str, Any] = {
        "current_version": node.version,
        "target_version": target,
        "node_id": node.node_id,
        "profile": node.profile,
        "active_stacks": len(stacks),
        "steps": [
            {"order": 1, "action": "snapshot_state",
             "description": "Run aictl snapshot create --label pre-upgrade and record the snapshot ID"},
            {"order": 2, "action": "drain_workloads", "description": "Gracefully drain active stacks"},
            {"order": 3, "action": "stage_update",
             "description": "On a bootc host, run sudo bootc upgrade, or sudo bootc switch "
                            "with the full image reference for a specific version"},
            {"order": 4, "action": "apply_update",
             "description": "Confirm the update is staged, then reboot into it"},
            {"order": 5, "action": "verify_health",
             "description": "After reboot, review aictl doctor; if the node is unhealthy, "
                            "stop and follow the manual rollback steps"},
            {"order": 6, "action": "restore_workloads",
             "description": "Re-apply stacks from saved state and verify them with aictl ps"},
        ],
        "rollback": "Manual: run sudo bootc rollback, then reboot. aictl does not roll back automatically.",
        "rollback_steps": [
            "Run sudo bootc rollback and confirm it succeeds before rebooting.",
            "Reboot into the previous deployment and review aictl doctor.",
            "If aictl state needs recovery, run aictl snapshot restore <snapshot-id> "
            "with the pre-upgrade snapshot ID and the same state-directory settings.",
            "Re-apply any drained stacks as needed and verify them with aictl ps.",
        ],
        "estimated_downtime_seconds": 120,
        "generated_at": time.time(),
    }

    if getattr(args, "json", False):
        print_json(plan)
        return 0

    ok(f"Upgrade plan: {node.version} → {target}")
    print()
    for step in plan["steps"]:
        print(f"  {step['order']}. {step['description']}")
    print()
    print_kv([
        ("Active stacks", str(plan["active_stacks"])),
        ("Est. downtime", f"{plan['estimated_downtime_seconds']}s"),
        ("Rollback", plan["rollback"]),
    ])
    for order, step in enumerate(plan["rollback_steps"], 1):
        print(f"  {order}. {step}")

    return 0
