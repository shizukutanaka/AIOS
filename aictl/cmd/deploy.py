"""aictl deploy — zero-config model deployment (DGDR-style).

Inspired by NVIDIA Dynamo's DGDR: specify model + SLA → auto-deploy.
"""

from __future__ import annotations

from typing import Any

import argparse

import json
from aictl.core.constants import VLLM_IMAGE
from aictl.core.output import ok, print_json, print_kv
from aictl.runtime.dynamo import (
    DGDRSpec, detect_dynamo, generate_dgdr_yaml,
    estimate_dgdr_resources, generate_kvbm_config,
)


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("deploy", help="Zero-config model deployment")
    dsub = p.add_subparsers(dest="deploy_cmd")

    plan = dsub.add_parser("plan", help="Plan a deployment (estimate resources)")
    plan.add_argument("model", help="Model name (e.g. meta-llama/Llama-3.2-8B-Instruct)")
    plan.add_argument("--hardware", default="auto", help="GPU type")
    plan.add_argument("--ttft", type=int, default=500, help="Target TTFT p95 (ms)")
    plan.add_argument("--tps", type=int, default=100, help="Target throughput (tokens/sec)")
    plan.add_argument("--max-gpus", type=int, default=8, help="Max GPUs")
    plan.add_argument("--quant", default="auto", help="Quantization")
    plan.set_defaults(func=run_plan)

    manifest = dsub.add_parser("manifest", help="Generate deployment YAML")
    manifest.add_argument("model", help="Model name")
    manifest.add_argument("--hardware", default="auto")
    manifest.add_argument("--ttft", type=int, default=500)
    manifest.add_argument("--tps", type=int, default=100)
    manifest.set_defaults(func=run_manifest)

    dynamo = dsub.add_parser("dynamo", help="Check NVIDIA Dynamo availability")
    dynamo.set_defaults(func=run_dynamo)

    kvbm = dsub.add_parser("kvbm", help="Generate KVBM config")
    kvbm.set_defaults(func=run_kvbm)

    disagg = dsub.add_parser("disagg", help="Generate P/D disaggregated deployment (llm-d)")
    disagg.add_argument("model", help="Model name")
    disagg.add_argument("--prefill-replicas", type=int, default=1)
    disagg.add_argument("--decode-replicas", type=int, default=2)
    disagg.add_argument("--connector", default="NixlConnector",
                        choices=["NixlConnector", "LMCacheConnector"])
    disagg.add_argument("--image", default=VLLM_IMAGE)
    disagg.set_defaults(func=run_disagg)

    opt = dsub.add_parser("optimize", help="Generate optimal vLLM flags")
    opt.add_argument("model", help="Model name (e.g. meta-llama/Llama-3.1-8B-Instruct)")
    opt.add_argument("--size", type=float, default=0, help="Model size in billions (auto-detect)")
    opt.add_argument("--gpu", default="auto", help="GPU type (H100, B200, etc.)")
    opt.add_argument("--gpu-count", type=int, default=1)
    opt.add_argument("--vram", type=int, default=0, help="VRAM per GPU in MB")
    opt.add_argument("--objective", default="balanced",
                     choices=["balanced", "throughput", "interactivity"])
    opt.add_argument("--speculative", action="store_true")
    opt.set_defaults(func=run_optimize)

    ms = dsub.add_parser("modelservice", help="Generate llm-d ModelService Helm values")
    ms.add_argument("model", help="Model name")
    ms.add_argument("--preset", default="balanced", choices=["balanced", "latency", "throughput"])
    ms.add_argument("--replicas", type=int, default=1)
    ms.add_argument("--gpu-count", type=int, default=1, dest="ms_gpu_count")
    ms.add_argument("--tp", type=int, default=1, help="Tensor parallel size")
    ms.add_argument("--lora", nargs="*", default=[], help="LoRA adapter names")
    ms.set_defaults(func=run_modelservice)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_plan(args: argparse.Namespace) -> int:
    """Execute the plan subcommand."""
    spec = DGDRSpec(
        model=args.model, hardware=args.hardware,
        sla_ttft_ms=args.ttft, sla_throughput_tps=args.tps,
        max_gpus=args.max_gpus, quantization=args.quant,
    )
    est = estimate_dgdr_resources(spec)

    if getattr(args, "json", False):
        print_json(est)
        return 0

    ok(f"Deployment Plan: {args.model}")
    print()
    print_kv([
        ("Parameters", f"{est['model_params_b']:.0f}B"),
        ("Model VRAM", f"{est['model_vram_gb']:.1f} GB"),
        ("Total VRAM", f"{est['total_vram_gb']:.1f} GB (model + KV cache)"),
        ("GPUs needed", f"{est['gpus_needed']}x {est['gpu_type']}"),
        ("Estimated TPS", f"{est['estimated_tps']} tokens/sec"),
        ("Meets SLA", "\u2713" if est["meets_sla"] else "\u2717"),
        ("P/D disagg", "recommended" if est["disagg_recommended"] else "not needed"),
    ], indent=2)
    return 0


def run_manifest(args: argparse.Namespace) -> int:
    """Execute the manifest subcommand."""
    spec = DGDRSpec(
        model=args.model, hardware=args.hardware,
        sla_ttft_ms=args.ttft, sla_throughput_tps=args.tps,
    )
    manifest = generate_dgdr_yaml(spec)
    print(json.dumps(manifest, indent=2))
    return 0


def run_dynamo(args: argparse.Namespace) -> int:
    """Execute the dynamo subcommand."""
    status = detect_dynamo()

    if getattr(args, "json", False):
        print_json(status)
        return 0

    ok("NVIDIA Dynamo Status")
    print()
    for key, val in status.items():
        icon = "\u2713" if val else "\u2717"
        if isinstance(val, bool):
            print(f"  {icon} {key}")
        elif val:
            print(f"  {icon} {key}: {val}")
    return 0


def run_kvbm(args: argparse.Namespace) -> int:
    """Execute the kvbm subcommand."""
    from dataclasses import asdict
    config = generate_kvbm_config()

    if getattr(args, "json", False):
        print_json(asdict(config))
        return 0

    ok("KVBM Configuration (4-tier memory)")
    print()
    print_kv([
        ("G1: GPU HBM", f"{config.gpu_hbm_gb:.1f} GB"),
        ("G2: CPU DRAM", f"{config.cpu_dram_gb:.1f} GB"),
        ("G3: Local SSD", f"{config.local_ssd_gb:.1f} GB"),
        ("G4: Remote", f"{config.remote_storage_gb:.1f} GB"),
        ("Block size", f"{config.block_size_tokens} tokens"),
        ("NIXL backend", config.nixl_backend),
        ("GPUDirect Storage", "\u2713" if config.nixl_enable_gds else "\u2717"),
    ], indent=2)
    return 0


def run_disagg(args: argparse.Namespace) -> int:
    """Generate P/D disaggregated deployment manifests."""
    from aictl.stack.disagg import DisaggConfig, generate_disagg_manifests

    config = DisaggConfig(
        model=args.model,
        prefill_replicas=args.prefill_replicas,
        decode_replicas=args.decode_replicas,
        kv_connector=args.connector,
        image=args.image,
    )
    manifests = generate_disagg_manifests(config)

    if getattr(args, "json", False):
        print_json(manifests)
        return 0

    k8s_list = {"apiVersion": "v1", "kind": "List", "items": manifests}
    print(json.dumps(k8s_list, indent=2))

    ok(f"Generated {len(manifests)} resources for P/D disaggregation")
    ok(f"  Prefill: {config.prefill_replicas} replicas")
    ok(f"  Decode:  {config.decode_replicas} replicas")
    ok(f"  KV connector: {config.kv_connector}")
    return 0


def run_optimize(args: argparse.Namespace) -> int:
    """Generate optimal vLLM serve flags."""
    from aictl.runtime.optimize import (
        optimize_vllm_flags, flags_to_command, HardwareProfile, GPU_CC,
    )

    # Auto-detect GPU
    gpu_name = args.gpu
    vram = args.vram
    if gpu_name == "auto":
        from aictl.runtime.broker import full_detect
        hw = full_detect()
        if hw.gpus:
            gpu_name = hw.gpus[0].name
            vram = hw.gpus[0].vram_mb
        else:
            gpu_name = "CPU"
            vram = 0

    # Auto-detect model size from name
    model_size = args.size
    if model_size == 0:
        # Heuristic from model name
        import re
        m = re.search(r'(\d+)[bB]', args.model)
        if m:
            model_size = float(m.group(1))
        else:
            model_size = 8.0  # Default

    profile = HardwareProfile(
        gpu_name=gpu_name,
        gpu_count=args.gpu_count,
        vram_per_gpu_mb=vram or 24576,
        compute_capability=GPU_CC.get(gpu_name, 0),
    )

    result = optimize_vllm_flags(
        model=args.model,
        model_size_b=model_size,
        hardware=profile,
        objective=args.objective,
        enable_speculative=getattr(args, "speculative", False),
    )

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json(asdict(result))
        return 0

    ok(f"Optimized vLLM flags for {args.model}")
    print()
    print(flags_to_command(result))
    print()

    if result.notes:
        ok("Optimizations applied:")
        for note in result.notes:
            print(f"  • {note}")

    if result.estimated_throughput_tps:
        print(f"\n  Estimated: ~{result.estimated_throughput_tps} tokens/sec")

    return 0


def run_modelservice(args: argparse.Namespace) -> int:
    """Generate llm-d ModelService Helm values."""
    from aictl.stack.modelservice import ModelServiceConfig, generate_helm_values, values_to_yaml

    config = ModelServiceConfig(
        model=args.model,
        preset=args.preset,
        replicas=args.replicas,
        gpu_count=getattr(args, "ms_gpu_count", 1),
        tensor_parallel=getattr(args, "tp", 1),
        enable_lora=bool(getattr(args, "lora", [])),
        lora_adapters=getattr(args, "lora", []),
    )

    values = generate_helm_values(config)

    if getattr(args, "json", False):
        print_json(values)
        return 0

    yaml = values_to_yaml(values)
    print(f"# llm-d ModelService Helm values for {args.model}")
    print(f"# Preset: {args.preset}")
    print(f"# Install: helm install {args.model.split('/')[-1].lower()} \\")
    print("#   oci://ghcr.io/llm-d/charts/modelservice -f values.yaml")
    print()
    print(yaml)

    ok(f"Generated ModelService values ({args.preset} preset)")
    return 0
