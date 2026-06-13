"""aictl model — model registry and pull."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, print_json, print_table
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("model", help="Model management")
    msub = p.add_subparsers(dest="model_cmd")

    ls = msub.add_parser("list", help="List registered models")
    ls.set_defaults(func=run_list)

    reg = msub.add_parser("register", help="Register a model")
    reg.add_argument("name", help="Model name")
    reg.add_argument("--digest", default="", help="Expected digest")
    reg.add_argument("--format", default="gguf", dest="fmt")
    reg.add_argument("--signed", action="store_true")
    reg.set_defaults(func=run_register)

    cache = msub.add_parser("cache", help="Model cache management")
    cache.add_argument("--clean", action="store_true", help="Remove stale entries")
    cache.add_argument("--days", type=int, default=30, help="Stale threshold (days)")
    cache.set_defaults(func=run_cache)

    pull = msub.add_parser("pull", help="Pull model from OCI registry")
    pull.add_argument("reference", help="OCI reference (e.g. ghcr.io/org/model:tag)")
    pull.add_argument("--output", default="", help="Output directory")
    pull.set_defaults(func=run_pull)

    verify = msub.add_parser("verify", help="Verify model/image signature")
    verify.add_argument("reference", help="Image or model reference")
    verify.add_argument("--key", default="", help="Public key file for verification")
    verify.set_defaults(func=run_verify)

    ps = msub.add_parser("ps", help="Show models currently loaded in GPU memory")
    ps.add_argument("--json", action="store_true", help="JSON output")
    ps.set_defaults(func=run_ps)

    inspect = msub.add_parser("inspect", help="Show full metadata for a registered model")
    inspect.add_argument("name", help="Model name or id")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=run_inspect)

    cleanup = msub.add_parser("cleanup", help="Remove stale model registry entries")
    cleanup.add_argument("--days", type=int, default=30,
                         help="Remove models not updated in N days (default: 30)")
    cleanup.add_argument("--status", default="",
                         help="Only remove entries with this status (e.g. unavailable)")
    cleanup.add_argument("--dry-run", action="store_true",
                         help="Show what would be removed without deleting")
    cleanup.set_defaults(func=run_cleanup)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_list(args: argparse.Namespace) -> int:
    """Execute the list subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    models = store.list_models()

    if getattr(args, "json", False):
        print_json(models)
        return 0

    if not models:
        print("No models registered. Use: aictl model register <name>")
        return 0

    print_table(models, ["id", "name", "format", "signed", "status"])
    return 0


def run_register(args: argparse.Namespace) -> int:
    """Execute the register subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    import uuid
    mid = uuid.uuid4().hex[:8]
    store.register_model(
        model_id=mid,
        name=args.name,
        digest=args.digest,
        fmt=args.fmt,
        signed=args.signed,
    )

    if getattr(args, "json", False):
        print_json({"id": mid, "name": args.name})
        return 0

    ok(f"Model registered: {args.name} (id={mid})")
    return 0


def run_cache(args: argparse.Namespace) -> int:
    """Execute the cache subcommand."""
    from aictl.runtime.cache import scan_cache, clean_stale, format_bytes

    report = scan_cache()

    if getattr(args, "json", False):
        print_json({
            "total_bytes": report.total_bytes,
            "locations": report.locations,
            "entries": len(report.entries),
        })
        return 0

    print(f"Model cache: {format_bytes(report.total_bytes)}")
    for src, size in report.locations.items():
        if size > 0:
            print(f"  {src}: {format_bytes(size)}")

    if report.entries:
        print(f"\n  {len(report.entries)} cached models/files")

    if getattr(args, "clean", False):
        stale = clean_stale(report, days=args.days, dry_run=False)
        if stale:
            print(f"\n  Removed {len(stale)} stale files "
                  f"(>{args.days} days), freed {format_bytes(sum(e.size_bytes for e in stale))}")
            for e in stale[:5]:
                print(f"    {e.name} ({format_bytes(e.size_bytes)})")
        else:
            ok("No stale cache entries")

    return 0


def run_pull(args: argparse.Namespace) -> int:
    """Execute the pull subcommand."""
    from aictl.trust.oras import pull_model, oras_available
    from aictl.core.output import warn

    if not oras_available():
        warn("oras not installed — trying fallback (skopeo/podman)")

    ok(f"Pulling {args.reference}...")
    result = pull_model(args.reference, output_dir=getattr(args, "output", ""))

    if getattr(args, "json", False):
        print_json({"success": result.success, "path": result.local_path,
                     "digest": result.digest, "error": result.error})
        return 0 if result.success else 1

    if result.success:
        ok(f"Model pulled to {result.local_path}")
        if result.digest:
            print(f"  Digest: {result.digest}")
    else:
        err(f"Pull failed: {result.error}")
    return 0 if result.success else 1


def run_verify(args: argparse.Namespace) -> int:
    """Execute the verify subcommand."""
    from aictl.trust.cosign import verify_image, cosign_available

    if not cosign_available():
        err("cosign not installed — cannot verify signatures")
        return 1

    ok(f"Verifying {args.reference}...")
    result = verify_image(args.reference, public_key=getattr(args, "key", ""))

    if getattr(args, "json", False):
        print_json({"verified": result.verified, "method": result.method,
                     "signer": result.signer, "error": result.error})
        return 0 if result.verified else 1

    if result.verified:
        ok(f"Signature verified ({result.method})")
        if result.signer:
            print(f"  Signer: {result.signer}")
    else:
        err(f"Verification failed: {result.error}")
    return 0 if result.verified else 1


def run_ps(args: argparse.Namespace) -> int:
    """Show models currently loaded in GPU memory across all configured engines."""
    import json
    import urllib.request
    from aictl.core.state import StateStore
    from aictl.core.config import load_config

    store = StateStore(getattr(args, "state_dir", None))
    config = load_config(store.dir)
    engines = config.engines.to_dict()

    loaded: list[dict] = []

    for engine_name, base_url in engines.items():
        base_url = base_url.rstrip("/")

        # Ollama: /api/ps returns running models with VRAM usage
        if "11434" in base_url or engine_name == "ollama":
            try:
                with urllib.request.urlopen(f"{base_url}/api/ps", timeout=3) as r:
                    data = json.loads(r.read())
                for m in data.get("models", []):
                    loaded.append({
                        "engine": engine_name,
                        "model": m.get("name", ""),
                        "vram_mb": m.get("size_vram", 0) // (1024 * 1024),
                        "expires_at": m.get("expires_at", ""),
                    })
            except Exception:
                pass
            continue

        # vLLM / SGLang: /v1/models (OpenAI-compatible)
        try:
            req = urllib.request.Request(
                f"{base_url}/v1/models",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read())
            for m in data.get("data", []):
                loaded.append({
                    "engine": engine_name,
                    "model": m.get("id", ""),
                    "vram_mb": 0,  # vLLM/SGLang don't expose per-model VRAM via API
                    "expires_at": "",
                })
        except Exception:
            pass

    if getattr(args, "json", False):
        print_json(loaded)
        return 0

    if not loaded:
        print("No models currently loaded (no engines reachable or no models active)")
        return 0

    print_table(loaded, ["engine", "model", "vram_mb", "expires_at"])
    return 0


def run_cleanup(args: argparse.Namespace) -> int:
    """Remove stale model registry entries by age or status."""
    import time as _time
    store = StateStore(getattr(args, "state_dir", None))
    models = store.list_models()
    days = getattr(args, "days", 30)
    status_filter = getattr(args, "status", "")
    dry_run = getattr(args, "dry_run", False)
    cutoff = _time.time() - days * 86400

    candidates = [
        m for m in models
        if (m.get("registered_at", 0) < cutoff)
        and (not status_filter or m.get("status", "") == status_filter)
    ]

    if not candidates:
        print("No stale model entries found.")
        if getattr(args, "json", False):
            print_json({"removed": 0, "dry_run": dry_run, "candidates": []})
        return 0

    if getattr(args, "json", False):
        print_json({
            "removed": 0 if dry_run else len(candidates),
            "dry_run": dry_run,
            "candidates": [{"id": m.get("id"), "name": m.get("name"),
                             "status": m.get("status"), "registered_at": m.get("registered_at")}
                            for m in candidates],
        })
        if not dry_run:
            _delete_models(store, [m["id"] for m in candidates])
        return 0

    action = "Would remove" if dry_run else "Removing"
    ok(f"{action} {len(candidates)} stale model entries (>{days} days old)")
    for m in candidates:
        reg = _time.strftime("%Y-%m-%d", _time.localtime(m.get("registered_at", 0)))
        print(f"  - {m.get('name')} [{m.get('status')}] registered {reg}")

    if not dry_run:
        _delete_models(store, [m["id"] for m in candidates])
        ok("Cleanup complete")
    else:
        print("\n  (dry-run — no changes made)")
    return 0


def _delete_models(store: StateStore, model_ids: list[str]) -> None:
    """Delete model registry entries by ID."""
    db = store._db()
    try:
        for mid in model_ids:
            db.execute("DELETE FROM models WHERE id=?", (mid,))
        db.commit()
    finally:
        db.close()


def run_inspect(args: argparse.Namespace) -> int:
    """Show full metadata for a registered model by name or id."""
    store = StateStore(getattr(args, "state_dir", None))
    models = store.list_models()
    query = args.name.lower()
    match = next(
        (m for m in models if m.get("name", "").lower() == query
         or m.get("id", "").startswith(query)),
        None,
    )

    if match is None:
        err(f"Model not found: {args.name}")
        if getattr(args, "json", False):
            print_json({"found": False, "name": args.name})
        return 1

    if getattr(args, "json", False):
        print_json(match)
        return 0

    import time as _time
    reg_time = match.get("registered_at", 0)
    reg_str = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(reg_time)) if reg_time else "unknown"
    size = match.get("size_bytes", 0)
    size_str = f"{size / 1_073_741_824:.2f} GB" if size >= 1_073_741_824 else (
        f"{size / 1_048_576:.1f} MB" if size >= 1_048_576 else f"{size} B"
    )

    print(f"Model: {match.get('name', '')}")
    print(f"  id          : {match.get('id', '')}")
    print(f"  format      : {match.get('format', match.get('fmt', ''))}")
    print(f"  status      : {match.get('status', '')}")
    print(f"  size        : {size_str}")
    print(f"  signed      : {bool(match.get('signed', False))}")
    if match.get("signer"):
        print(f"  signer      : {match.get('signer')}")
    if match.get("digest"):
        print(f"  digest      : {match.get('digest')}")
    print(f"  registered  : {reg_str}")
    return 0
