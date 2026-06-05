"""aictl cluster — cluster promotion and management."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, warn, print_json
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("cluster", help="Cluster management")
    csub = p.add_subparsers(dest="cluster_cmd")

    promote = csub.add_parser("promote", help="Plan K3s promotion")
    promote.add_argument("--dry-run", action="store_true")
    promote.set_defaults(func=run_promote)

    export = csub.add_parser("export", help="Export stack as KServe manifests")
    export.add_argument("stack", help="Stack name")
    export.set_defaults(func=run_export)

    gw = csub.add_parser("gateway", help="Export as Gateway API Inference Extension")
    gw.add_argument("stack", help="Stack name")
    gw.add_argument("--class", dest="gw_class", default="istio",
                    choices=["istio", "nginx", "kgateway", "gke"])
    gw.set_defaults(func=run_gateway)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_promote(args: argparse.Namespace) -> int:
    """Execute the promote subcommand."""
    from aictl.runtime.k3s import generate_promote_plan
    store = StateStore(getattr(args, "state_dir", None))
    plan = generate_promote_plan(store)

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json(asdict(plan))
        return 0

    if not plan.ready:
        err(f"Not ready: {plan.reason}")
        return 1

    ok(f"K3s promotion plan ({plan.reason})")
    print()
    for step in plan.steps:
        print(f"  {step.order}. {step.description}")
        if step.command:
            print(f"     $ {step.command}")
    if plan.warnings:
        print()
        for w in plan.warnings:
            warn(w)
    return 0


def run_export(args: argparse.Namespace) -> int:
    """Execute the export subcommand."""
    import json as json_mod
    store = StateStore(getattr(args, "state_dir", None))

    # Try KServe LLMInferenceService format first
    from aictl.stack.manifest import get_recipe
    manifest = get_recipe(args.stack)
    if manifest:
        from aictl.stack.kserve import stack_to_llmisvc, LLMISvcConfig
        config = LLMISvcConfig(
            performance_mode="balanced",
            enable_prefix_caching=True,
        )
        resources = stack_to_llmisvc(manifest, config)
        k8s_list = {"apiVersion": "v1", "kind": "List", "items": resources}
        print(json_mod.dumps(k8s_list, indent=2))
        return 0

    # Fallback: convert from state
    from aictl.runtime.k3s import stack_to_k8s
    manifests = stack_to_k8s(args.stack, store)
    if not manifests or not manifests.get("items"):
        err(f"Stack '{args.stack}' not found")
        return 1
    print(json_mod.dumps(manifests, indent=2))
    return 0


def run_gateway(args: argparse.Namespace) -> int:
    """Export stack as Gateway API Inference Extension resources."""
    import json as json_mod
    from aictl.stack.manifest import get_recipe
    from aictl.stack.gateway import stack_to_gateway_api, GatewayInferenceConfig

    manifest = get_recipe(args.stack)
    if not manifest:
        err(f"Recipe '{args.stack}' not found")
        return 1

    config = GatewayInferenceConfig(
        gateway_class=getattr(args, "gw_class", "istio"),
    )
    resources = stack_to_gateway_api(manifest, config)

    if getattr(args, "json", False):
        print_json(resources)
        return 0

    k8s_list = {"apiVersion": "v1", "kind": "List", "items": resources}
    print(json_mod.dumps(k8s_list, indent=2))
    return 0
