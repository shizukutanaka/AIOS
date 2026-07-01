"""aictl tenant — multi-tenant isolation management."""

from __future__ import annotations

from typing import Any

import argparse

import json
from pathlib import Path
from aictl.core.output import ok, err, print_json, print_kv, print_table
from aictl.core.atomicio import atomic_write_text
from aictl.core.filelock import file_lock
from aictl.core.tenant import (
    TENANT_CLASSES, Tenant,
    generate_k8s_namespace, generate_cgroup_limits,
    registry_path as _core_registry_path, load_registry as _core_load_registry,
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

    ls = tsub.add_parser("list", help="List provisioned tenants")
    ls.set_defaults(func=run_list)

    create = tsub.add_parser("create", help="Provision a new tenant")
    create.add_argument("tenant_id", help="Unique tenant identifier")
    create.add_argument("--name", default="", help="Human-readable name")
    create.add_argument("--class", dest="tenant_class", default="standard",
                        choices=list(TENANT_CLASSES.keys()))
    create.set_defaults(func=run_create)

    delete = tsub.add_parser("delete", help="Remove a provisioned tenant")
    delete.add_argument("tenant_id", help="Tenant ID to remove")
    delete.set_defaults(func=run_delete)

    inspect = tsub.add_parser("inspect", help="Show full metadata for a tenant")
    inspect.add_argument("tenant_id", help="Tenant ID")
    inspect.set_defaults(func=run_inspect)

    link = tsub.add_parser(
        "link-key", help="Associate an API key with a tenant "
                         "(enables tenant-class rate limiting in `aictl proxy`).")
    link.add_argument("tenant_id", help="Tenant ID")
    link.add_argument("key_id", help="API key ID (from `aictl apikey list`)")
    link.set_defaults(func=run_link_key)

    unlink = tsub.add_parser("unlink-key", help="Remove an API key -> tenant association.")
    unlink.add_argument("tenant_id", help="Tenant ID")
    unlink.add_argument("key_id", help="API key ID to unlink")
    unlink.set_defaults(func=run_unlink_key)

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


# ── persistent tenant registry helpers ───────────────────────────────────────
# Thin wrappers over aictl.core.tenant's registry_path/load_registry — the
# single source of truth shared with daemon/proxy.py's tenant-by-API-key
# lookup, so the CLI and the live proxy always agree on where tenants live and
# never diverge in how a corrupt/non-dict registry file degrades.

def _registry_path(args: argparse.Namespace) -> Path:
    return _core_registry_path(getattr(args, "state_dir", None))


def _load_registry(path: Path) -> dict:
    return _core_load_registry(path)


def _save_registry(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, indent=2))


# ── lifecycle commands ────────────────────────────────────────────────────────

def run_list(args: argparse.Namespace) -> int:
    """List all provisioned tenants."""
    import time as _time
    reg = _load_registry(_registry_path(args))

    if getattr(args, "json", False):
        print_json(list(reg.values()))
        return 0

    if not reg:
        print("No tenants provisioned. Use: aictl tenant create <id>")
        return 0

    rows = [{"id": t["id"], "name": t["name"], "class": t["tenant_class"],
             "created": _time.strftime("%Y-%m-%d", _time.localtime(t.get("created_at", 0)))}
            for t in reg.values()]
    print_table(rows, ["id", "name", "class", "created"])
    return 0


def _norm_id(raw: str) -> str:
    """Normalize a tenant id. Leading/trailing whitespace is never part of the
    identity, so a tenant created as 'team ' must be findable as 'team'. An
    empty/whitespace-only id is invalid (callers reject it)."""
    return (raw or "").strip()


def run_create(args: argparse.Namespace) -> int:
    """Provision a new tenant."""
    import time as _time
    tenant_id = _norm_id(args.tenant_id)
    if not tenant_id:
        err("Tenant ID is required (empty or whitespace-only is not allowed).")
        return 1
    path = _registry_path(args)
    # Serialize load→modify→save against concurrent tenant create/delete.
    with file_lock(path):
        reg = _load_registry(path)

        if tenant_id in reg:
            err(f"Tenant already exists: {tenant_id}")
            return 1

        record = {
            "id": tenant_id,
            "name": getattr(args, "name", "") or tenant_id,
            "tenant_class": getattr(args, "tenant_class", "standard"),
            "created_at": _time.time(),
        }
        reg[tenant_id] = record
        _save_registry(path, reg)

    if getattr(args, "json", False):
        print_json(record)
        return 0

    ok(f"Tenant created: {tenant_id} (class: {record['tenant_class']})")
    return 0


def run_delete(args: argparse.Namespace) -> int:
    """Remove a provisioned tenant."""
    tenant_id = _norm_id(args.tenant_id)
    path = _registry_path(args)
    with file_lock(path):
        reg = _load_registry(path)

        if tenant_id not in reg:
            if getattr(args, "json", False):
                print_json({"deleted": False, "tenant_id": tenant_id,
                            "error": f"Tenant not found: {tenant_id}"})
                return 1
            err(f"Tenant not found: {tenant_id}")
            return 1

        del reg[tenant_id]
        _save_registry(path, reg)
    if getattr(args, "json", False):
        print_json({"deleted": True, "tenant_id": tenant_id})
        return 0
    ok(f"Tenant deleted: {tenant_id}")
    return 0


def run_link_key(args: argparse.Namespace) -> int:
    """Associate an API key with a tenant so the proxy can rate-limit by
    tenant class, not just per-key. Without this link, `tenant create` and
    the class-based rate limiter (TenantRateLimiter) never actually connect
    to any real request — a tenant record with no linked keys is inert."""
    tenant_id = _norm_id(args.tenant_id)
    key_id = _norm_id(args.key_id)
    if not key_id:
        err("Key ID is required (empty or whitespace-only is not allowed).")
        return 1
    path = _registry_path(args)
    with file_lock(path):
        reg = _load_registry(path)
        if tenant_id not in reg:
            err(f"Tenant not found: {tenant_id}")
            return 1
        record = reg[tenant_id]
        key_ids = record.setdefault("api_key_ids", [])
        if key_id not in key_ids:
            key_ids.append(key_id)
        _save_registry(path, reg)

    if getattr(args, "json", False):
        print_json({"tenant_id": tenant_id, "linked": key_id,
                    "api_key_ids": reg[tenant_id]["api_key_ids"]})
        return 0
    ok(f"Linked key {key_id} to tenant {tenant_id}")
    return 0


def run_unlink_key(args: argparse.Namespace) -> int:
    """Remove an API key -> tenant association."""
    tenant_id = _norm_id(args.tenant_id)
    key_id = _norm_id(args.key_id)
    path = _registry_path(args)
    with file_lock(path):
        reg = _load_registry(path)
        if tenant_id not in reg:
            err(f"Tenant not found: {tenant_id}")
            return 1
        record = reg[tenant_id]
        key_ids = record.setdefault("api_key_ids", [])
        if key_id in key_ids:
            key_ids.remove(key_id)
        _save_registry(path, reg)

    if getattr(args, "json", False):
        print_json({"tenant_id": tenant_id, "unlinked": key_id,
                    "api_key_ids": reg[tenant_id]["api_key_ids"]})
        return 0
    ok(f"Unlinked key {key_id} from tenant {tenant_id}")
    return 0


def run_inspect(args: argparse.Namespace) -> int:
    """Show full metadata for a tenant."""
    import time as _time
    tenant_id = _norm_id(args.tenant_id)
    path = _registry_path(args)
    reg = _load_registry(path)

    if tenant_id not in reg:
        err(f"Tenant not found: {tenant_id}")
        if getattr(args, "json", False):
            print_json({"found": False, "tenant_id": tenant_id})
        return 1

    record = reg[tenant_id]
    tc = TENANT_CLASSES.get(record.get("tenant_class", "standard"))

    if getattr(args, "json", False):
        out = dict(record)
        if tc:
            from dataclasses import asdict
            out["class_limits"] = asdict(tc)
        print_json(out)
        return 0

    ok(f"Tenant: {record['id']}")
    created_ts = record.get("created_at", 0)
    print_kv([
        ("id",           record["id"]),
        ("name",         record.get("name", "")),
        ("class",        record.get("tenant_class", "standard")),
        ("created",      _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(created_ts)) if created_ts else "—"),
    ], indent=2)
    if tc:
        print()
        print("  Class limits:")
        print_kv([
            ("max_gpu_slices", str(tc.max_gpu_slices)),
            ("max_memory_gb",  f"{tc.max_memory_gb} GB"),
            ("max_vram_gb",    f"{tc.max_vram_gb} GB"),
            ("max_rpm",        str(tc.max_requests_per_min)),
            ("audit_level",    tc.audit_level),
        ], indent=4)
    return 0
