"""aictl trust — model integrity baseline & drift detection (trust-on-first-use).

`aictl model verify` checks a model against a digest you were *given*. This
answers the complementary question after you've already trusted a model: have my
local model bytes changed since I trusted them (tampering / bit-rot / bad sync)?

  aictl trust baseline ./models/llama3-8b     # record digests now (first use)
  aictl trust check    ./models/llama3-8b     # re-hash and report any drift
  aictl trust list                            # show everything baselined

A plain baseline is blind trust-on-first-use: it hashes whatever bytes happen to
be on disk right now, with no claim about how they got there. `--source` tags
*why* this baseline should be trusted (`aictl model pull --baseline` tags it
`pull:<reference>` automatically); `check`/`list` surface it so you can tell a
freshly-pulled, registry-attested baseline apart from an untagged one recorded
at some arbitrary later point.
"""

from __future__ import annotations

import argparse
from typing import Any

from aictl.core.output import ok, warn, err, print_json, print_table
from aictl.core.state import StateStore
from aictl.trust.baseline import BaselineStore, worst_status


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser(
        "trust",
        help="Model integrity baseline & drift detection (trust-on-first-use).",
    )
    tsub = p.add_subparsers(dest="trust_cmd")

    base = tsub.add_parser("baseline", help="Record the SHA-256 baseline of a model.")
    base.add_argument("path", help="Model file or directory to baseline.")
    base.add_argument("--source", default="",
                      help="Provenance note for why this baseline should be "
                           "trusted (e.g. 'reviewed-by-secteam'). Blank = plain "
                           "trust-on-first-use, no provenance claim.")
    base.add_argument("--json", action="store_true")
    base.set_defaults(func=run_baseline)

    chk = tsub.add_parser("check", help="Check a model against its recorded baseline.")
    chk.add_argument("path", nargs="?", default="",
                     help="Model file or directory to check. Omit to audit EVERY "
                          "baselined file system-wide (what `aictl doctor --deep` "
                          "runs internally).")
    chk.add_argument("--json", action="store_true")
    chk.set_defaults(func=run_check)

    lst = tsub.add_parser("list", help="List all recorded baselines.")
    lst.add_argument("--json", action="store_true")
    lst.set_defaults(func=run_list)

    p.set_defaults(func=lambda a: run_check(a) if getattr(a, "path", None) else _help(p))


def _help(p: Any) -> int:
    p.print_help()
    return 0


def _store(args: argparse.Namespace) -> BaselineStore:
    sd = StateStore(getattr(args, "state_dir", None)).dir
    return BaselineStore(sd)


def run_baseline(args: argparse.Namespace) -> int:
    """Record the digest baseline for a model path."""
    store = _store(args)
    recorded = store.record(args.path, source=getattr(args, "source", ""))
    if not recorded:
        err(f"No model files found at: {args.path}")
        print("  Expected a model file or a directory containing weight files "
              "(.gguf/.safetensors/.bin/.pt/.pth/.onnx).")
        return 1

    if getattr(args, "json", False):
        print_json({"recorded": recorded, "count": len(recorded)})
        return 0

    ok(f"Baselined {len(recorded)} file(s) from {args.path}")
    for r in recorded:
        tag = f"  [{r['source']}]" if r.get("source") else ""
        print(f"  {r['digest']}  {r['path']}{tag}")
    return 0


def run_check(args: argparse.Namespace) -> int:
    """Check a model path against its recorded baseline.

    No path -> audit EVERY baselined file system-wide (what `aictl doctor
    --deep` calls internally): "has anything I've ever promised to watch
    drifted?", without the caller having to already know a specific target.
    """
    store = _store(args)
    path = getattr(args, "path", "")
    if path:
        results = store.check(path)
        if not results:
            err(f"No model files found at: {path}")
            return 1
    else:
        results = store.check_all()
        if not results:
            err("No baselines recorded yet. Run: aictl trust baseline <model-path>")
            return 1

    status = worst_status(results)
    if getattr(args, "json", False):
        print_json({"status": status, "results": results})
        # Drift / missing → non-zero so CI and scripts can gate on it.
        return 0 if status in ("ok", "new") else 2

    icon = {"ok": "✓", "changed": "✗", "missing": "✗", "new": "○"}
    rows = []
    for r in results:
        if r["status"] == "new":
            source_display = "—"           # not baselined at all yet
        else:
            source_display = r.get("source", "") or "(untagged)"
        rows.append({
            "status": f"{icon.get(r['status'], '?')} {r['status']}",
            "file": _short(r["path"]),
            "source": source_display,
        })
    print_table(rows, ["status", "file", "source"])
    print()

    n_changed = sum(1 for r in results if r["status"] == "changed")
    n_missing = sum(1 for r in results if r["status"] == "missing")
    n_new = sum(1 for r in results if r["status"] == "new")
    if n_changed or n_missing:
        err(f"Integrity drift detected: {n_changed} changed, {n_missing} missing. "
            f"These model files differ from their trusted baseline.")
        return 2
    if n_new:
        warn(f"{n_new} file(s) not yet baselined. Run: aictl trust baseline {path}")
        return 0
    ok("All files match their trusted baseline.")
    return 0


def run_list(args: argparse.Namespace) -> int:
    """List all recorded baselines."""
    store = _store(args)
    data = store.list_all()
    if getattr(args, "json", False):
        print_json({"baselines": data, "count": len(data)})
        return 0
    if not data:
        print("No baselines recorded yet. Run: aictl trust baseline <model-path>")
        return 0
    rows = [{"digest": v["digest"][:23] + "…", "file": _short(k),
            "source": v.get("source", "") or "(untagged)"}
            for k, v in sorted(data.items())]
    print_table(rows, ["digest", "file", "source"])
    return 0


def _short(path: str, width: int = 48) -> str:
    """Trim a long path from the left for display."""
    return path if len(path) <= width else "…" + path[-(width - 1):]
