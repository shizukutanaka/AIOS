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
