"""aictl scale — autoscaling configuration and KEDA manifest generation."""

from __future__ import annotations

from typing import Any

import argparse

import json


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("scale", help="Autoscaling management")
    ssub = p.add_subparsers(dest="scale_cmd")

    keda = ssub.add_parser("keda", help="Generate KEDA ScaledObject")
    keda.add_argument("deployment", help="K8s deployment name")
    keda.add_argument("--engine", default="vllm", choices=["vllm", "sglang"])
    keda.add_argument("--min", type=int, default=1)
    keda.add_argument("--max", type=int, default=8)
    keda.add_argument("--threshold", type=int, default=5, help="Queue depth threshold")
    keda.add_argument("--prometheus", default="http://prometheus:9090")
    keda.set_defaults(func=run_keda)

    hpa = ssub.add_parser("hpa", help="Generate standard K8s HPA")
    hpa.add_argument("deployment", help="K8s deployment name")
    hpa.add_argument("--min", type=int, default=1)
    hpa.add_argument("--max", type=int, default=8)
    hpa.set_defaults(func=run_hpa)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_keda(args: argparse.Namespace) -> int:
    """Execute the keda subcommand."""
    from aictl.runtime.autoscaler import generate_keda_scaled_object, ScalePolicy
    policy = ScalePolicy(
        min_replicas=args.min, max_replicas=args.max,
        queue_depth_threshold=args.threshold,
    )
    obj = generate_keda_scaled_object(
        args.deployment, engine=args.engine,
        prometheus_url=args.prometheus, policy=policy,
    )
    print(json.dumps(obj, indent=2))
    return 0


def run_hpa(args: argparse.Namespace) -> int:
    """Execute the hpa subcommand."""
    from aictl.runtime.autoscaler import generate_hpa_manifest, ScalePolicy
    policy = ScalePolicy(min_replicas=args.min, max_replicas=args.max)
    obj = generate_hpa_manifest(args.deployment, policy=policy)
    print(json.dumps(obj, indent=2))
    return 0
