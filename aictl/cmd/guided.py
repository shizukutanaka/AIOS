"""aictl guided — structured / guided-decoding advisor.

Make the model emit valid JSON (or regex/choice/grammar-constrained) output.
aictl produces schema-shaped data everywhere but never advised on *forcing*
the model to do the same. This advisor recommends a guided-decoding backend
per engine and prints ready-to-paste serve flags + a request snippet. It can
also validate a JSON document against a JSON Schema locally (zero deps).

Usage:
  aictl guided recommend --engine vllm        # best backend + flags
  aictl guided matrix                          # engine → backend support matrix
  aictl guided validate data.json --schema s.json   # local JSON-Schema check

Source: arxiv.org/abs/2501.10868 (JSONSchemaBench)
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from aictl.core.output import ok, warn, err, print_json
from aictl.runtime.guided import (
    BACKEND_INFO, ENGINE_BACKENDS, CONSTRAINT_KINDS,
    recommend_backend, vllm_flags, sglang_flags, request_example,
    validate_json_schema,
)


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "guided",
        help="Structured/guided decoding advisor: force valid JSON output.",
    )
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sp = p.add_subparsers(dest="guided_cmd", required=False)

    r = sp.add_parser("recommend", help="Best backend + flags for an engine.")
    r.add_argument("--engine", default="vllm",
                   help="vllm | sglang | tensorrt-llm | ollama")
    r.add_argument("--kind", default="json_schema", choices=CONSTRAINT_KINDS,
                   help="Constraint kind to emit a request example for.")
    r.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    r.set_defaults(func=run_recommend)

    m = sp.add_parser("matrix", help="Engine → backend support matrix.")
    m.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    m.set_defaults(func=run_matrix)

    v = sp.add_parser("validate", help="Validate a JSON doc against a schema (local).")
    v.add_argument("data", help="Path to JSON instance, or '-' for stdin.")
    v.add_argument("--schema", required=True, help="Path to JSON Schema file.")
    v.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    v.set_defaults(func=run_validate)

    p.set_defaults(func=run_default)


def run_default(args: argparse.Namespace) -> int:
    """Show help."""
    print()
    print("  aictl guided — Structured / Guided Decoding Advisor")
    print()
    print("  Force the model to emit valid JSON / regex / choice output.")
    print("  XGrammar is the 2026 default (vLLM/SGLang/TensorRT-LLM, <40µs/tok).")
    print()
    print("    aictl guided recommend --engine vllm   # backend + serve flags")
    print("    aictl guided matrix                     # support matrix")
    print("    aictl guided validate data.json --schema s.json")
    print()
    return 0


def run_recommend(args: argparse.Namespace) -> int:
    """Recommend a backend and print ready-to-paste flags for an engine."""
    engine = args.engine.lower()
    kind = getattr(args, "kind", "json_schema")
    use_json = getattr(args, "json", False)

    if engine not in ENGINE_BACKENDS:
        err(f"Unknown engine: {args.engine}")
        print(f"  Known: {', '.join(ENGINE_BACKENDS)}")
        return 1

    backend = recommend_backend(engine)
    example = request_example(engine, kind)

    if engine == "ollama":
        # Ollama is engine-native (the `format` field), no pluggable backend.
        if use_json:
            print_json({"engine": engine, "backend": None, "native": True,
                        "flags": None, "request_example": example})
            return 0
        print()
        ok("Ollama: structured output is engine-native")
        print()
        print("  Ollama enforces JSON via the top-level `format` field — no")
        print("  serve flag needed. Pass a JSON Schema (or \"json\") per request:")
        print()
        for line in example.splitlines():
            print(f"    {line}")
        print()
        return 0

    flags = vllm_flags(backend) if engine in ("vllm", "tensorrt-llm") else sglang_flags(backend)
    speed, requirement, when = BACKEND_INFO[backend]

    if use_json:
        print_json({
            "engine": engine, "backend": backend, "native": False,
            "serve_flags": flags, "relative_speed": speed,
            "requirement": requirement, "when_to_use": when,
            "alternatives": ENGINE_BACKENDS[engine][1:],
            "request_example": example,
        })
        return 0

    print()
    ok(f"Recommended backend for {engine}: {backend.upper()}")
    print()
    print(f"  Why:  {when}")
    print(f"  Req:  {requirement}")
    print()
    print("  Serve flag:")
    if engine == "sglang":
        print(f"    python -m sglang.launch_server --model <model> {flags}")
    else:
        print(f"    vllm serve <model> {flags}")
    print()
    print(f"  Request ({kind}):")
    for line in example.splitlines():
        print(f"    {line}")
    print()
    alts = ENGINE_BACKENDS[engine][1:]
    if alts:
        print(f"  Alternatives: {', '.join(alts)}")
    print("  Source: arxiv.org/abs/2501.10868 (JSONSchemaBench)\n")
    return 0


def run_matrix(args: argparse.Namespace) -> int:
    """Print the engine → backend support matrix."""
    use_json = getattr(args, "json", False)

    matrix = []
    for engine, backends in ENGINE_BACKENDS.items():
        matrix.append({
            "engine": engine,
            "backends": backends or ["(engine-native `format` field)"],
            "recommended": recommend_backend(engine),
            "native": not backends,
        })

    if use_json:
        print_json({"matrix": matrix, "backends": {
            b: {"relative_speed": s, "requirement": req, "when_to_use": w}
            for b, (s, req, w) in BACKEND_INFO.items()}})
        return 0

    print()
    print("  Guided-decoding support matrix")
    print()
    print(f"  {'ENGINE':<14} {'RECOMMENDED':<14} BACKENDS")
    print(f"  {'-'*14} {'-'*14} {'-'*40}")
    for row in matrix:
        rec = row["recommended"] or "native"
        backends = ", ".join(row["backends"])
        print(f"  {row['engine']:<14} {rec:<14} {backends}")
    print()
    print(f"  {'BACKEND':<20} {'SPEED':<7} REQUIREMENT")
    print(f"  {'-'*20} {'-'*7} {'-'*40}")
    for b, (speed, req, _when) in BACKEND_INFO.items():
        print(f"  {b:<20} {'★'*speed:<7} {req}")
    print()
    print("  XGrammar is the 2026 default across vLLM/SGLang/TensorRT-LLM.\n")
    return 0


def run_validate(args: argparse.Namespace) -> int:
    """Validate a JSON instance against a JSON Schema (local, zero-dep)."""
    use_json = getattr(args, "json", False)

    try:
        if args.data == "-":
            import sys
            instance = json.load(sys.stdin)
        else:
            with open(args.data, encoding="utf-8") as f:
                instance = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        err(f"Cannot read instance: {e}")
        return 1

    try:
        with open(args.schema, encoding="utf-8") as f:
            schema = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        err(f"Cannot read schema: {e}")
        return 1

    errors = validate_json_schema(instance, schema)

    if use_json:
        print_json({"valid": not errors, "errors": errors})
        return 0 if not errors else 2

    if not errors:
        ok("Valid — instance conforms to schema.")
        return 0
    warn(f"Invalid — {len(errors)} error(s):")
    for e in errors:
        print(f"    • {e}")
    print()
    return 2
