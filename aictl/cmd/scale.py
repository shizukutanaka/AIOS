"""aictl scale — autoscaling configuration and KEDA manifest generation."""

from __future__ import annotations

from typing import Any

import argparse

import json

from aictl.runtime.adapters import discover_engines
from aictl.runtime.autoscaler import AutoScaler
from aictl.core.output import print_json, print_table


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

    status = ssub.add_parser("status", help="Show live autoscaling status for all engines")
    status.add_argument("--engine", default="", help="Filter by engine type")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=run_status)

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


def run_status(args: argparse.Namespace) -> int:
    """Show live autoscaling decisions for all configured engines."""
    import time

    engine_filter = getattr(args, "engine", "")
    healths = discover_engines()
    if engine_filter:
        healths = [h for h in healths if h.engine == engine_filter]

    results = []
    for h in healths:
        scaler = AutoScaler(h.engine, h.endpoint)
        decision = scaler.evaluate()
        results.append({
            "engine": h.engine,
            "endpoint": h.endpoint,
            "reachable": h.reachable,
            "action": decision.action,
            "current_replicas": decision.current_replicas,
            "desired_replicas": decision.desired_replicas,
            "reason": decision.reason,
            "metrics": decision.metrics,
            "evaluated_at": time.strftime(
                "%H:%M:%S", time.localtime(decision.timestamp)
            ),
        })

    if getattr(args, "json", False):
        print_json(results)
        return 0

    if not results:
        print("No engines found for autoscaling status.")
        return 0

    for r in results:
        icon = "→" if r["action"] != "none" else "·"
        print(f"  {icon} {r['engine']}  {r['endpoint']}")
        print(f"      replicas: {r['current_replicas']} → {r['desired_replicas']}"
              f"  action={r['action']}")
        if r["reason"]:
            print(f"      reason: {r['reason']}")
        if r["metrics"]:
            m = r["metrics"]
            print(f"      metrics: queue={m.get('queue_depth', 0):.0f} "
                  f"kv={m.get('kv_cache_util', 0):.2f} "
                  f"active={m.get('active_requests', 0):.0f}")
    return 0
