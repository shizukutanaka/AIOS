"""aictl init — initialize a single-node AI OS instance."""

from __future__ import annotations

from typing import Any

import argparse

import time
import uuid

from aictl.core.state import StateStore, NodeState
from aictl.core.output import ok, err, print_json, print_kv
from aictl.runtime.broker import full_detect


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("init", help="Initialize local AI OS node")
    p.add_argument("--force", action="store_true", help="Re-initialize")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the init command."""
    store = StateStore(getattr(args, "state_dir", None))

    if store.is_initialized() and not getattr(args, "force", False):
        err("Already initialized. Use --force to re-initialize.")
        return 1

    report = full_detect()

    node = NodeState(
        node_id=uuid.uuid4().hex[:12],
        hostname=report.system.hostname,
        initialized_at=time.time(),
        profile=report.profile,
        mode="local",
        gpu_count=len(report.gpus),
        vram_total_mb=sum(g.vram_mb for g in report.gpus),
        ram_total_mb=report.system.ram_total_mb,
    )
    store.save_node(node)

    if getattr(args, "json", False):
        print_json(node)
    else:
        ok(f"Node initialized: {node.node_id}")
        print_kv([
            ("Hostname", node.hostname),
            ("Profile", node.profile),
            ("GPUs", f"{node.gpu_count} ({node.vram_total_mb} MB VRAM)"),
            ("RAM", f"{node.ram_total_mb} MB"),
            ("Container RT", report.container_runtime),
            ("State dir", str(store.dir)),
        ])

        if report.issues:
            print()
            for issue in report.issues:
                err(issue)
        if report.recommendations:
            print()
            for rec in report.recommendations:
                print(f"  → {rec}")

    return 0
