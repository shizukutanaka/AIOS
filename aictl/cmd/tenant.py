"""aictl tenant — multi-tenant isolation management."""

from __future__ import annotations

from typing import Any

import argparse

import json
from aictl.core.output import ok, print_json, print_table
from aictl.core.tenant import (
    TENANT_CLASSES, Tenant,
    generate_k8s_namespace, generate_cgroup_limits,
)


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("tenant", help="Multi-tenant isolation")
    tsub = p.add_subparsers(dest="tenant_cmd")

    classes = tsub.add_parser("classes", help="List available tenant classes")
    classes.set_defaults(func=run_classes)

    ns = tsub.add_parser("namespace", help="Generate K8s namespace + quotas")
    ns.add_argument("tenant_id", help="Tenant ID")
    ns.add_argument("--class", dest="tenant_class", default="standard",
                    choices=list(TENANT_CLASSES.keys()))
    ns.set_defaults(func=run_namespace)

    cgroup = tsub.add_parser("cgroup", help="Show cgroup limits for local mode")
    cgroup.add_argument("--class", dest="tenant_class", default="standard")
    cgroup.set_defaults(func=run_cgroup)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_classes(args: argparse.Namespace) -> int:
    """Execute the classes subcommand."""
    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json({k: asdict(v) for k, v in TENANT_CLASSES.items()})
        return 0

    rows = []
    for name, tc in TENANT_CLASSES.items():
        rows.append({
            "class": name,
            "gpu": tc.max_gpu_slices,
            "ram": f"{tc.max_memory_gb}GB",
            "vram": f"{tc.max_vram_gb}GB",
            "rpm": tc.max_requests_per_min,
            "signed": "\u2713" if tc.require_signed_models else "",
            "audit": tc.audit_level,
        })
    print_table(rows, ["class", "gpu", "ram", "vram", "rpm", "signed", "audit"])
    return 0


def run_namespace(args: argparse.Namespace) -> int:
    """Execute the namespace subcommand."""
    tenant = Tenant(id=args.tenant_id, name=args.tenant_id,
                    tenant_class=args.tenant_class)
    manifests = generate_k8s_namespace(tenant)
    if getattr(args, "json", False):
        print_json(manifests)
    else:
        print(json.dumps(manifests, indent=2))
    return 0


def run_cgroup(args: argparse.Namespace) -> int:
    """Execute the cgroup subcommand."""
    tenant = Tenant(id="local", name="local",
                    tenant_class=getattr(args, "tenant_class", "standard"))
    limits = generate_cgroup_limits(tenant)

    if getattr(args, "json", False):
        print_json(limits)
        return 0

    ok(f"cgroup v2 limits for '{args.tenant_class}' class")
    print()
    for k, v in limits.items():
        print(f"  {k:20s} {v}")
    return 0
