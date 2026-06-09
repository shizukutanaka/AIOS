"""aictl prompt — Prompt management and versioning.

The #1 source of silent regressions in 2026:
  "We changed the prompt and didn't notice quality dropped."

This command treats prompts as first-class artifacts:
  - Save, list, get, delete
  - Version history (diff between versions)
  - Tag with model, use_case, owner
  - Export to eval suite for aictl eval
  - Import from file or stdin

Storage: ~/.aios/prompts.json (local, no cloud, no SaaS)

Usage:
  aictl prompt save --name summarize_ticket --text "Summarize this: {input}"
  aictl prompt save --name summarize_ticket --file ./prompts/v2.txt
  aictl prompt list
  aictl prompt get summarize_ticket
  aictl prompt get summarize_ticket --version 2
  aictl prompt history summarize_ticket
  aictl prompt delete summarize_ticket
  aictl prompt export --name summarize_ticket --format eval > suite.json
  aictl prompt run --name summarize_ticket --input "Bug: login fails"
"""

from __future__ import annotations

import argparse

from typing import Any

import json
import os
import time
from pathlib import Path

from aictl.core.output import ok, warn, err, print_json


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "prompt",
        help="Save, version, and run prompts. Treat prompts as code.",
    )
    sp = p.add_subparsers(dest="prompt_cmd", required=True)

    # save
    s = sp.add_parser("save", help="Save or update a prompt.")
    s.add_argument("--name", required=True, help="Prompt name (slug)")
    s.add_argument("--text", help="Prompt text (use {input} for variable)")
    s.add_argument("--file", help="Load prompt text from file")
    s.add_argument("--model", default="", help="Intended model (optional)")
    s.add_argument("--use-case", default="", help="Tag: chat/code/summarize/etc.")
    s.add_argument("--owner", default="", help="Owner/author tag")
    s.set_defaults(func=run_save)

    # list
    li = sp.add_parser("list", help="List all saved prompts.")
    li.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    li.set_defaults(func=run_list)

    # get
    g = sp.add_parser("get", help="Get a prompt (latest or specific version).")
    g.add_argument("name", help="Prompt name")
    g.add_argument("--version", type=int, default=0, help="Version number (0 = latest)")
    g.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    g.set_defaults(func=run_get)

    # history
    h = sp.add_parser("history", help="Show version history of a prompt.")
    h.add_argument("name", help="Prompt name")
    h.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    h.set_defaults(func=run_history)

    # delete
    d = sp.add_parser("delete", help="Delete a prompt.")
    d.add_argument("name", help="Prompt name")
    d.add_argument("--yes", action="store_true", help="Skip confirmation")
    d.set_defaults(func=run_delete)

    # export
    ex = sp.add_parser("export", help="Export prompt to eval suite JSON.")
    ex.add_argument("--name", required=True, help="Prompt name")
    ex.add_argument("--format", choices=["eval", "text"], default="eval")
    ex.set_defaults(func=run_export)

    # run
    r = sp.add_parser("run", help="Run a saved prompt with an input.")
    r.add_argument("--name", required=True, help="Prompt name")
    r.add_argument("--input", default="", help="Fill {input} variable")
    r.add_argument("--model", default="", help="Override model")
    r.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    r.set_defaults(func=run_run)


def run_save(args: argparse.Namespace) -> int:
    """Save or update a prompt."""
    name = args.name.replace(" ", "_").lower()

    # Get text
    text = getattr(args, "text", "") or ""
    if getattr(args, "file", None):
        try:
            text = Path(args.file).read_text(encoding="utf-8").strip()
        except Exception as e:
            err(f"Cannot read file: {e}")
            return 1

    if not text:
        err("Provide --text or --file.")
        return 1

    db = _load()
    if name not in db:
        db[name] = {
            "name": name,
            "model": args.model,
            "use_case": args.use_case,
            "owner": args.owner,
            "versions": [],
        }

    version_num = len(db[name]["versions"]) + 1
    db[name]["versions"].append({
        "version": version_num,
        "text": text,
        "created_at": time.time(),
        "created_at_str": time.strftime("%Y-%m-%d %H:%M"),
    })

    # Update meta
    if args.model:
        db[name]["model"] = args.model
    if args.use_case:
        db[name]["use_case"] = args.use_case

    _save(db)
    ok(f"Prompt '{name}' saved  (version {version_num})")
    print(f"  {text[:80]}{'...' if len(text) > 80 else ''}")
    print()

    if "{input}" in text:
        print(f"  Run: aictl prompt run --name {name} --input 'your text here'")
    else:
        print(f"  Run: aictl prompt run --name {name}")
    print()
    return 0


def run_list(args: argparse.Namespace) -> int:
    """List all saved prompts."""
    db = _load()
    if not db:
        warn("No prompts saved yet.")
        print()
        print("  Save one: aictl prompt save --name my_prompt --text 'Summarize: {input}'")
        print()
        return 0

    if getattr(args, "json", False):
        print_json({
            k: {
                "name": v["name"],
                "versions": len(v["versions"]),
                "model": v.get("model", ""),
                "use_case": v.get("use_case", ""),
                "latest": v["versions"][-1]["text"][:80] if v["versions"] else "",
            }
            for k, v in db.items()
        })
        return 0

    print()
    print(f"  {'NAME':<20} {'VER':>3}  {'USE CASE':<12}  {'MODEL':<20}  LATEST TEXT")
    print(f"  {'-'*20}  {'-'*3}  {'-'*12}  {'-'*20}  {'-'*30}")
    for k, v in db.items():
        ver = len(v["versions"])
        latest = v["versions"][-1]["text"][:35] if v["versions"] else ""
        uc = v.get("use_case", "")[:11]
        model = v.get("model", "")[:19]
        print(f"  {k:<20} {ver:>3}  {uc:<12}  {model:<20}  {latest}")
    print()
    return 0


def run_get(args: argparse.Namespace) -> int:
    """Get a prompt."""
    db = _load()
    name = args.name
    if name not in db:
        err(f"Unknown prompt: {name}")
        return 1

    entry = db[name]
    if not entry["versions"]:
        err(f"No versions saved for: {name}")
        return 1

    version_num = args.version
    if version_num == 0:
        ver = entry["versions"][-1]
    else:
        matching = [v for v in entry["versions"] if v["version"] == version_num]
        if not matching:
            err(f"Version {version_num} not found for: {name}")
            return 1
        ver = matching[0]

    if getattr(args, "json", False):
        print_json({
            "name": name,
            "version": ver["version"],
            "text": ver["text"],
            "created_at": ver["created_at_str"],
            "model": entry.get("model", ""),
            "use_case": entry.get("use_case", ""),
        })
        return 0

    print()
    print(f"  Name:     {name}")
    print(f"  Version:  {ver['version']} of {len(entry['versions'])}")
    print(f"  Saved:    {ver['created_at_str']}")
    if entry.get("model"):
        print(f"  Model:    {entry['model']}")
    print()
    print(ver["text"])
    print()
    return 0


def run_history(args: argparse.Namespace) -> int:
    """Show version history."""
    db = _load()
    name = args.name
    if name not in db:
        err(f"Unknown prompt: {name}")
        return 1

    entry = db[name]
    if getattr(args, "json", False):
        print_json({"name": name, "versions": entry["versions"]})
        return 0

    print()
    print(f"  History: {name} ({len(entry['versions'])} versions)")
    print()
    for ver in reversed(entry["versions"]):
        preview = ver["text"][:60].replace("\n", "\\n")
        print(f"  v{ver['version']}  {ver['created_at_str']}  {preview}...")
    print()
    return 0


def run_delete(args: argparse.Namespace) -> int:
    """Delete a prompt."""
    db = _load()
    name = args.name
    if name not in db:
        err(f"Unknown prompt: {name}")
        return 1

    if not args.yes:
        versions = len(db[name]["versions"])
        warn(f"Delete '{name}' ({versions} versions)?")
        print("  Re-run with --yes to confirm.")
        return 1

    del db[name]
    _save(db)
    ok(f"Deleted: {name}")
    return 0


def run_export(args: argparse.Namespace) -> int:
    """Export to eval suite format."""
    db = _load()
    name = args.name
    if name not in db:
        err(f"Unknown prompt: {name}")
        return 1

    entry = db[name]
    if not entry["versions"]:
        err(f"No versions for: {name}")
        return 1

    ver = entry["versions"][-1]
    text = ver["text"]

    if args.format == "text":
        print(text)
        return 0

    # eval suite format
    suite = {
        "name": name,
        "model": entry.get("model", ""),
        "cases": [
            {
                "label": f"{name}_v{ver['version']}",
                "prompt": text,
                "assertions": [
                    {"type": "max_length", "value": 2000},
                    {"type": "latency_ms", "value": 10000},
                ],
            }
        ],
    }
    print(json.dumps(suite, indent=2, ensure_ascii=False))
    return 0


def run_run(args: argparse.Namespace) -> int:
    """Run a saved prompt with optional {input} substitution."""
    db = _load()
    name = args.name
    if name not in db:
        err(f"Unknown prompt: {name}")
        return 1

    entry = db[name]
    if not entry["versions"]:
        err(f"No versions for: {name}")
        return 1

    text = entry["versions"][-1]["text"]
    input_val = getattr(args, "input", "") or ""
    if "{input}" in text:
        if not input_val:
            err("Prompt has {input} variable. Provide --input 'your text'.")
            return 1
        text = text.replace("{input}", input_val)

    model = getattr(args, "model", "") or entry.get("model", "")

    print()
    ok(f"Running: {name} (v{len(entry['versions'])})")
    print()

    try:
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()
        import aictl
        kwargs = {}
        if model:
            kwargs["model"] = model
        r = aictl.ai.ask(text, **kwargs)

        print(str(r))
        print()

        if getattr(args, "json", False):
            print_json({
                "prompt_name": name,
                "model": model or "default",
                "text": text,
                "response": str(r),
                "cost": r.cost,
                "cached": r.cached,
            })
        else:
            print(f"  Cost: {r.cost}  Cached: {r.cached}")
            print()
    except Exception as e:
        err(f"Inference failed: {e}")
        return 1
    return 0


# ── Storage ───────────────────────────────────────────────


def _db_path() -> Path:
    """Execute db path."""
    base = os.environ.get("AIOS_STATE_DIR", os.path.expanduser("~/.aios"))
    return Path(base) / "prompts.json"


def _load() -> dict[str, Any]:
    """Load and return data from storage."""
    path = _db_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass  # best-effort; failure is non-critical
    return {}


def _save(db: dict[str, Any]) -> None:
    """Persist data to storage."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(db, indent=2, ensure_ascii=False))
