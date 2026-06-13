"""aictl recipe — list and run built-in AI recipes."""

from __future__ import annotations

from typing import Any

import argparse

import time

from aictl.core.output import ok, err, print_json
from aictl.core.state import StateStore, StackEntry
from aictl.stack.manifest import list_recipes, get_recipe
from aictl.stack.orchestrator import apply_stack


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("recipe", help="Built-in AI recipes")
    rsub = p.add_subparsers(dest="recipe_cmd")

    ls = rsub.add_parser("list", help="List available recipes")
    ls.set_defaults(func=run_list)

    run_p = rsub.add_parser("run", help="Run a recipe")
    run_p.add_argument("name", help="Recipe name")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.set_defaults(func=run_recipe)

    val = rsub.add_parser("validate", help="Validate a recipe's configuration")
    val.add_argument("name", nargs="?", default="",
                     help="Recipe name (validates all recipes if omitted)")
    val.add_argument("--json", action="store_true", help="JSON output")
    val.set_defaults(func=run_validate)

    test_p = rsub.add_parser("test", help="Test a recipe with dry-run and resource checks")
    test_p.add_argument("name", help="Recipe name to test")
    test_p.set_defaults(func=run_test)

    export_p = rsub.add_parser("export", help="Export a recipe manifest to a JSON file")
    export_p.add_argument("name", help="Recipe name")
    export_p.add_argument("--output", default="", help="Output file (default: <name>.json)")
    export_p.set_defaults(func=run_export)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


KNOWN_RUNTIMES = {"vllm", "ollama", "sglang", "trt-llm", "auto"}


def validate_manifest(manifest: Any) -> list[str]:
    """Return a list of validation problems for a stack manifest (empty = valid)."""
    problems: list[str] = []

    if not manifest.name:
        problems.append("manifest has no name")
    if not manifest.services:
        problems.append("manifest has no services")

    seen_names: set[str] = set()
    seen_ports: dict[int, str] = {}
    for svc in manifest.services:
        if not svc.name:
            problems.append("a service has no name")
            continue
        if svc.name in seen_names:
            problems.append(f"duplicate service name: {svc.name}")
        seen_names.add(svc.name)

        if svc.runtime not in KNOWN_RUNTIMES:
            problems.append(
                f"{svc.name}: unknown runtime '{svc.runtime}' "
                f"(expected one of {', '.join(sorted(KNOWN_RUNTIMES))})")

        if svc.replicas < 1:
            problems.append(f"{svc.name}: replicas must be >= 1 (got {svc.replicas})")

        if svc.port:
            if not (1 <= svc.port <= 65535):
                problems.append(f"{svc.name}: port {svc.port} out of range (1-65535)")
            elif svc.port in seen_ports:
                problems.append(
                    f"{svc.name}: port {svc.port} already used by {seen_ports[svc.port]}")
            else:
                seen_ports[svc.port] = svc.name

        if svc.gpu_memory_mb < 0:
            problems.append(f"{svc.name}: gpu_memory_mb cannot be negative")

        # vllm/sglang/trt-llm are model-serving runtimes — they need a model
        if svc.runtime in ("vllm", "sglang", "trt-llm") and not svc.model:
            problems.append(f"{svc.name}: runtime '{svc.runtime}' requires a model")

    return problems


def run_validate(args: argparse.Namespace) -> int:
    """Validate one or all recipes' configurations."""
    target = getattr(args, "name", "")
    if target:
        names = [target]
        if get_recipe(target) is None:
            err(f"Unknown recipe: {target}")
            if getattr(args, "json", False):
                print_json({"valid": False, "error": f"unknown recipe: {target}"})
            return 1
    else:
        names = list_recipes()

    results = []
    all_valid = True
    for name in names:
        manifest = get_recipe(name)
        problems = validate_manifest(manifest) if manifest else ["recipe not found"]
        valid = not problems
        all_valid = all_valid and valid
        results.append({"recipe": name, "valid": valid, "problems": problems})

    if getattr(args, "json", False):
        print_json({"all_valid": all_valid, "results": results})
        return 0 if all_valid else 1

    for r in results:
        if r["valid"]:
            ok(f"{r['recipe']}: valid")
        else:
            err(f"{r['recipe']}: {len(r['problems'])} problem(s)")
            for p in r["problems"]:
                print(f"    - {p}")

    return 0 if all_valid else 1


def run_list(args: argparse.Namespace) -> int:
    """Execute the list subcommand."""
    names = list_recipes()
    if getattr(args, "json", False):
        print_json(names)
        return 0

    print("Available recipes:")
    for name in names:
        m = get_recipe(name)
        svc_count = len(m.services) if m else 0
        gpu = any(s.gpu_required for s in (m.services if m else []))
        tag = " [GPU]" if gpu else ""
        print(f"  {name} — {svc_count} services{tag}")

    print("\nRun with: aictl recipe run <name>")
    return 0


def run_test(args: argparse.Namespace) -> int:
    """Test a recipe: validate + dry-run + resource checks."""
    manifest = get_recipe(args.name)
    if manifest is None:
        err(f"Unknown recipe: {args.name}")
        if getattr(args, "json", False):
            print_json({"passed": False, "error": f"unknown recipe: {args.name}"})
        return 1

    checks: list[dict] = []

    # Structural validation
    problems = validate_manifest(manifest)
    checks.append({
        "check": "structural_validation",
        "passed": not problems,
        "detail": problems if problems else "OK",
    })

    # GPU resource check
    gpu_required = any(s.gpu_required for s in manifest.services)
    if gpu_required:
        from aictl.runtime.broker import full_detect
        hw = full_detect()
        has_gpu = len(hw.gpus) > 0
        checks.append({
            "check": "gpu_available",
            "passed": has_gpu,
            "detail": f"{len(hw.gpus)} GPU(s) detected" if has_gpu else "no GPUs found (recipe requires GPU)",
        })

    # Dry-run apply (applies no real changes)
    try:
        results = apply_stack(manifest, dry_run=True)
        dry_run_ok = all(r.status in ("dry-run", "running", "starting") for r in results)
        checks.append({
            "check": "dry_run_apply",
            "passed": dry_run_ok,
            "detail": f"{len(results)} service(s) would start",
        })
    except Exception as exc:
        checks.append({"check": "dry_run_apply", "passed": False, "detail": str(exc)})

    passed = all(c["passed"] for c in checks)

    if getattr(args, "json", False):
        print_json({"recipe": args.name, "passed": passed, "checks": checks})
        return 0 if passed else 1

    ok(f"Recipe test: {args.name}") if passed else err(f"Recipe test FAILED: {args.name}")
    for c in checks:
        icon = "✓" if c["passed"] else "✗"
        detail = c["detail"] if isinstance(c["detail"], str) else "; ".join(c["detail"])
        print(f"  {icon} {c['check']}: {detail}")
    return 0 if passed else 1


def run_export(args: argparse.Namespace) -> int:
    """Export a recipe manifest to a portable JSON file."""
    import json as _json
    from dataclasses import asdict
    manifest = get_recipe(args.name)
    if manifest is None:
        err(f"Unknown recipe: {args.name}")
        if getattr(args, "json", False):
            print_json({"exported": False, "error": f"unknown recipe: {args.name}"})
        return 1

    data = asdict(manifest)
    output = getattr(args, "output", "") or f"{args.name}.json"
    try:
        from pathlib import Path
        Path(output).write_text(_json.dumps(data, indent=2))
    except OSError as exc:
        err(f"Cannot write to {output}: {exc}")
        return 1

    if getattr(args, "json", False):
        print_json({"exported": True, "recipe": args.name, "output": output})
        return 0

    ok(f"Recipe '{args.name}' exported to {output}")
    svc_count = len(manifest.services)
    print(f"  services: {svc_count}")
    return 0


def run_recipe(args: argparse.Namespace) -> int:
    """Execute the recipe subcommand."""
    store = StateStore(getattr(args, "state_dir", None))
    manifest = get_recipe(args.name)

    if manifest is None:
        err(f"Unknown recipe: {args.name}")
        print(f"Available: {', '.join(list_recipes())}")
        return 1

    dry = getattr(args, "dry_run", False)
    results = apply_stack(manifest, dry_run=dry)

    entry = StackEntry(
        name=manifest.name,
        file=manifest.source_file,
        applied_at=time.time(),
        status="running" if not dry else "dry-run",
        services=[{"name": r.name, "status": r.status, "endpoint": r.endpoint} for r in results],
    )
    if not dry:
        store.upsert_stack(entry)

    if getattr(args, "json", False):
        print_json({"recipe": args.name, "services": [r.__dict__ for r in results]})
        return 0

    label = "[DRY RUN] " if dry else ""
    ok(f"{label}Recipe '{args.name}' started")
    for r in results:
        icon = "✓" if r.status in ("running", "starting", "dry-run") else "✗"
        ep = f" → {r.endpoint}" if r.endpoint else ""
        print(f"  {icon} {r.name} [{r.status}]{ep}")

    return 0
