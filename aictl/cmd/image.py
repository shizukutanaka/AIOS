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


def run_formats(args: argparse.Namespace) -> int:
    """Execute the formats subcommand."""
    from aictl.deploy.imagebuilder import list_formats
    formats = list_formats()
    if getattr(args, "json", False):
        print_json(formats)
        return 0
    print_table(formats, ["format", "description", "use"])
    return 0
