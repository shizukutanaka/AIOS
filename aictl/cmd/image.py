"""aictl image — build bootc disk images."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, print_json, print_table


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("image", help="Build bootc disk images")
    isub = p.add_subparsers(dest="image_cmd")

    build = isub.add_parser("build", help="Build a disk image")
    build.add_argument("--source", default="ghcr.io/shizukutanaka/aios:latest",
                       help="Source container image")
    build.add_argument("--format", default="qcow2", choices=["qcow2", "raw", "vmdk", "iso", "ami", "vhd"])
    build.add_argument("--output", default="./output", help="Output directory")
    build.set_defaults(func=run_build)

    formats = isub.add_parser("formats", help="List supported output formats")
    formats.set_defaults(func=run_formats)

    prune = isub.add_parser("prune", help="Remove dangling container images")
    prune.add_argument("--all", action="store_true", dest="all_images",
                       help="Remove all stopped container images (not just dangling)")
    prune.add_argument("--dry-run", action="store_true", help="Show what would be removed")
    prune.add_argument("--json", action="store_true", help="JSON output")
    prune.set_defaults(func=run_prune)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_build(args: argparse.Namespace) -> int:
    """Execute the build subcommand."""
    from aictl.deploy.imagebuilder import build_disk_image

    ok(f"Building {args.format} from {args.source}...")
    result = build_disk_image(
        container_image=args.source,
        output_dir=args.output,
        format=args.format,
    )

    if getattr(args, "json", False):
        print_json(result.__dict__)
        return 0 if result.success else 1

    if result.success:
        from aictl.runtime.cache import format_bytes
        ok(f"Image built: {result.output_path} ({format_bytes(result.size_bytes)})")
        print(f"  Duration: {result.duration_sec:.0f}s")
    else:
        err(f"Build failed: {result.error}")

    return 0 if result.success else 1


def run_prune(args: argparse.Namespace) -> int:
    """Remove dangling (or all stopped) container images via podman."""
    import subprocess
    import shutil

    dry = getattr(args, "dry_run", False)
    all_images = getattr(args, "all_images", False)

    if not shutil.which("podman"):
        err("podman not found — cannot prune images")
        if getattr(args, "json", False):
            print_json({"success": False, "error": "podman not found"})
        return 1

    # List images to be pruned first (for dry-run and reporting)
    list_cmd = ["podman", "images", "--filter", "dangling=true", "--format", "{{.ID}} {{.Repository}}:{{.Tag}} {{.Size}}"]
    if all_images:
        list_cmd = ["podman", "images", "--filter", "unused=true", "--format", "{{.ID}} {{.Repository}}:{{.Tag}} {{.Size}}"]

    try:
        proc = subprocess.run(list_cmd, capture_output=True, text=True, timeout=30)
        candidates = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except Exception as exc:
        err(f"Failed to list images: {exc}")
        return 1

    if not candidates:
        ok("No images to prune")
        if getattr(args, "json", False):
            print_json({"removed": [], "count": 0})
        return 0

    if dry:
        label = "[DRY RUN] "
        ok(f"{label}Would remove {len(candidates)} image(s):")
        for c in candidates:
            print(f"  - {c}")
        if getattr(args, "json", False):
            print_json({"dry_run": True, "would_remove": candidates, "count": len(candidates)})
        return 0

    prune_cmd = ["podman", "image", "prune", "--force"]
    if all_images:
        prune_cmd.append("--all")
    try:
        proc = subprocess.run(prune_cmd, capture_output=True, text=True, timeout=120)
        removed = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if proc.returncode != 0:
            err(f"Prune failed: {proc.stderr.strip()}")
            if getattr(args, "json", False):
                print_json({"success": False, "error": proc.stderr.strip()})
            return 1
        ok(f"Pruned {len(removed)} image(s)")
        if getattr(args, "json", False):
            print_json({"success": True, "removed": removed, "count": len(removed)})
        return 0
    except Exception as exc:
        err(f"Prune error: {exc}")
        return 1


def run_formats(args: argparse.Namespace) -> int:
    """Execute the formats subcommand."""
    from aictl.deploy.imagebuilder import list_formats
    formats = list_formats()
    if getattr(args, "json", False):
        print_json(formats)
        return 0
    print_table(formats, ["format", "description", "use"])
    return 0
