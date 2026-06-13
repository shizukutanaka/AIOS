"""aictl info — project information and system summary."""

from __future__ import annotations

from typing import Any

import argparse
from aictl.core.output import ok, print_json, print_kv


def _count_commands() -> int:
    """Count registered CLI commands dynamically."""
    try:
        from aictl.__main__ import build_parser
        p = build_parser()
        for action in p._actions:
            if hasattr(action, "choices") and action.choices:
                return len(action.choices)
    except Exception:
        pass  # best-effort; failure is non-critical
    return 75  # fallback


def _count_tests() -> str:
    """Count test methods dynamically."""
    try:
        import ast
        import os
        count = 0
        tests_dir = os.path.join(os.path.dirname(__file__), "..", "..", "tests")
        for f in os.listdir(tests_dir):
            if not f.startswith("test_") or not f.endswith(".py"):
                continue
            try:
                with open(os.path.join(tests_dir, f)) as _fh:
                    tree = ast.parse(_fh.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                        count += 1
            except Exception:
                pass  # best-effort; failure is non-critical
        return str(count) if count > 0 else "900+"
    except Exception:
        return "900+"


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("info", help="Project information")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the info command."""
    from aictl.__main__ import VERSION
    from aictl.stack.manifest import list_recipes
    from aictl.runtime.recommend import MODELS

    info = {
        "name": "AI Native Linux OS",
        "binary": "aictl",
        "version": VERSION,
        "python_commands": _count_commands(),
        "go_commands": 29,
        "rest_endpoints": 30,
        "recipes": len(list_recipes()),
        "model_db": len(MODELS),
        "skills": 8,
        "tests": _count_tests(),
        "new_v16": [
            "aictl fit — VRAM fit checker",
            "aictl quant — quantization advisor",
            "aictl troubleshoot — symptom diagnosis",
            "aictl rag — zero-config local RAG",
            "aictl guard — local PII + content filter",
            "aictl cache — semantic cache management",
            "aictl perf — per-command performance",
            "aictl dash — all-in-one dashboard",
            "aictl update — self-update + model refresh",
            "aictl help — discovery-oriented help",
        ],
        "stack": [
            "bootc v1.15 (Fedora 42)", "Podman + Quadlet",
            "vLLM v0.19 / SGLang v0.5 / Ollama v0.20",
            "K3s v1.35 + KServe v0.17 + llm-d v0.5",
            "NVIDIA Dynamo v0.8 (KVBM + NIXL)",
            "Gateway API InferencePool v1",
            "KEDA v2.19", "Cosign v3 + ORAS",
            "OTel GenAI SemConv v1.40 + Prometheus",
        ],
        "os_features": [
            "Fabric Memory Orchestrator (DRAM/CXL/NVMe)",
            "Attested Model Vault (Cosign v3)",
            "QoS Slice Broker + SLO Governor",
            "Context Continuity Engine",
            "Token Metering + Quota Enforcement",
            "Process Isolation (cgroup v2 OOM protection)",
            "LoRA Adapter Management",
            "NVIDIA Dynamo KVBM + NIXL",
        ],
    }

    if getattr(args, "json", False):
        print_json(info)
        return 0

    ok(f"aictl {info['version']}")
    print()
    print_kv([
        ("Commands", f"{info['python_commands']} Python + {info['go_commands']} Go"),
        ("REST API", f"{info['rest_endpoints']} endpoints"),
        ("Recipes", str(info['recipes'])),
        ("Models", str(info['model_db'])),
        ("Tests", info['tests']),
        ("Skills", str(info['skills'])),
    ], indent=2)

    print("\n  Stack:")
    for s in info["stack"]:
        print(f"    {s}")

    print("\n  OS Features:")
    for f in info["os_features"]:
        print(f"    \u2713 {f}")

    return 0
