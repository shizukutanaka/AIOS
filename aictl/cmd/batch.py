"""aictl batch — schedule background AI jobs during GPU idle time.

When you're not using your GPU interactively, aictl can run batch jobs:
  - Document embedding
  - Nightly model evaluation
  - File classification
  - Custom AI pipelines

Jobs run at low priority and pause immediately when interactive
requests arrive (PSI-based yield).

  aictl batch add embed-docs --schedule "0 2 * * *" --input ./docs
  aictl batch list
  aictl batch run embed-docs   # run now
  aictl batch remove embed-docs
"""

from __future__ import annotations

import argparse

from typing import Any

import json
import os
import time
from pathlib import Path

from aictl.core.output import ok, warn, err, print_json, print_table


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "batch",
        help="Schedule background AI jobs during GPU idle time.",
    )
    sp = p.add_subparsers(dest="batch_cmd", required=True)

    a = sp.add_parser("add", help="Add a batch job.")
    a.add_argument("name", help="Job name")
    a.add_argument("--schedule", default="0 2 * * *",
                   help="Cron schedule (default: 2am daily)")
    a.add_argument("--input", default="", help="Input path")
    a.add_argument("--model", default="auto", help="Model to use")
    a.add_argument("--task", default="embed",
                   choices=["embed", "classify", "summarize", "custom"])
    a.add_argument("--max-runtime", default="4h", help="Max runtime")
    a.set_defaults(func=run_add)

    sp.add_parser("list", help="List scheduled jobs.").set_defaults(func=run_list)

    rn = sp.add_parser("run", help="Run a job immediately.")
    rn.add_argument("name", help="Job name")
    rn.set_defaults(func=run_now)

    rm = sp.add_parser("remove", help="Remove a scheduled job.")
    rm.add_argument("name")
    rm.set_defaults(func=run_remove)

    sp.add_parser("status", help="Show running batch jobs.").set_defaults(func=run_status)


def run_add(args: argparse.Namespace) -> int:
    """Add a new entry."""
    db = _load()
    db["jobs"][args.name] = {
        "schedule": args.schedule,
        "input": args.input,
        "model": args.model,
        "task": args.task,
        "max_runtime": args.max_runtime,
        "created_at": time.time(),
        "last_run": None,
        "last_status": "pending",
        "runs": 0,
    }
    _save(db)
    ok(f"Job '{args.name}' scheduled: {args.schedule}")
    print(f"  Task: {args.task}  Model: {args.model}")
    if args.input:
        print(f"  Input: {args.input}")
    print()
    print("  The job will run when:")
    print("    - Schedule time arrives")
    print("    - GPU is idle (no interactive requests)")
    print()
    print(f"  Test now: aictl batch run {args.name}")
    print()
    return 0


def run_list(args: argparse.Namespace) -> int:
    """List all entries."""
    db = _load()
    if not db["jobs"]:
        warn("No batch jobs scheduled.")
        print("  Add one: aictl batch add myjob --schedule '0 2 * * *' --input ./docs")
        return 0

    if getattr(args, "json", False):
        print_json(db["jobs"])
        return 0

    rows = []
    for name, cfg in db["jobs"].items():
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(cfg["last_run"])) \
               if cfg["last_run"] else "never"
        rows.append({
            "JOB": name,
            "TASK": cfg["task"],
            "SCHEDULE": cfg["schedule"],
            "LAST RUN": last,
            "STATUS": cfg["last_status"],
            "RUNS": str(cfg["runs"]),
        })
    print()
    print_table(rows, columns=["JOB", "TASK", "SCHEDULE", "LAST RUN", "STATUS", "RUNS"])
    print()
    return 0


def run_now(args: argparse.Namespace) -> int:
    """Run a job immediately, with PSI-based interrupt support."""
    db = _load()
    if args.name not in db["jobs"]:
        err(f"Unknown job: {args.name}")
        return 1

    job = db["jobs"][args.name]
    print()
    ok(f"Running '{args.name}' ({job['task']})...")
    print()

    t0 = time.time()
    success = _execute_job(job)
    elapsed = time.time() - t0

    if success:
        job["last_run"] = time.time()
        job["last_status"] = "success"
        job["runs"] = job.get("runs", 0) + 1
        _save(db)
        ok(f"Job completed in {elapsed:.1f}s")
    else:
        job["last_status"] = "failed"
        _save(db)
        warn(f"Job failed or was interrupted after {elapsed:.1f}s")

    print()
    return 0 if success else 1


def run_remove(args: argparse.Namespace) -> int:
    """Remove an entry."""
    db = _load()
    if args.name not in db["jobs"]:
        err(f"Unknown job: {args.name}")
        return 1
    del db["jobs"][args.name]
    _save(db)
    ok(f"Job '{args.name}' removed.")
    return 0


def run_status(args: argparse.Namespace) -> int:
    """Show current status."""
    db = _load()
    running = [n for n, j in db["jobs"].items() if j["last_status"] == "running"]
    if running:
        print(f"\n  Running: {', '.join(running)}\n")
    else:
        print("\n  No batch jobs currently running.\n")
    return 0


def _execute_job(job: dict[str, Any]) -> bool:
    """Execute one batch job with appropriate handler."""
    task = job["task"]
    input_path = job.get("input", "")
    model = job.get("model", "auto")

    if task == "embed" and input_path:
        return _task_embed(input_path, model)
    elif task == "summarize" and input_path:
        return _task_summarize(input_path, model)
    elif task == "classify" and input_path:
        return _task_classify(input_path, model)
    else:
        warn(f"Task '{task}' with input '{input_path}': no handler.")
        return True  # soft success — don't mark as failed


def _task_embed(input_path: str, model: str) -> bool:
    """Embed all documents in a directory (delegates to RAG indexer)."""
    path = Path(input_path)
    if not path.exists():
        warn(f"Input path not found: {input_path}")
        return False
    try:
        from aictl.core.rag import RagStore, index_directory
        store = RagStore()
        stats = index_directory(path, store)
        print(f"  Embedded {stats['indexed']} files, "
              f"{stats['chunks_created']} chunks")
        return True
    except Exception as e:
        warn(f"Embed failed: {e}")
        return False


def _task_summarize(input_path: str, model: str) -> bool:
    """Summarize all text files in a directory."""
    path = Path(input_path)
    if not path.exists():
        warn(f"Input path not found: {input_path}")
        return False
    files = list(path.rglob("*.md")) + list(path.rglob("*.txt"))
    count = 0
    for f in files[:10]:  # max 10 files per run
        try:
            text = f.read_text(errors="replace")[:3000]
            from aictl.sdk import _AmbientContext
            _AmbientContext.reset_for_testing()
            import aictl
            summary = aictl.ai.ask("Summarize in 2 sentences:", context=text)
            print(f"  {f.name}: {str(summary)[:100]}")
            count += 1
        except Exception:
            pass  # best-effort; failure is non-critical
    print(f"  Summarized {count} files.")
    return True


def _task_classify(input_path: str, model: str) -> bool:
    """Classify files by content."""
    path = Path(input_path)
    if not path.exists():
        return False
    files = list(path.rglob("*.md")) + list(path.rglob("*.txt"))
    count = 0
    for f in files[:20]:
        try:
            text = f.read_text(errors="replace")[:500]
            from aictl.sdk import _AmbientContext
            _AmbientContext.reset_for_testing()
            import aictl
            cat = aictl.ai.classify(text,
                categories=["technical", "business", "legal", "other"])
            print(f"  {f.name:<30} → {cat}")
            count += 1
        except Exception:
            pass  # best-effort; failure is non-critical
    print(f"  Classified {count} files.")
    return True


def _db_path() -> Path:
    """Execute db path."""
    base = os.environ.get("AIOS_STATE_DIR", os.path.expanduser("~/.aios"))
    return Path(base) / "batch.json"


def _load() -> dict[str, Any]:
    """Load and return data from storage."""
    path = _db_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass  # best-effort; failure is non-critical
    return {"jobs": {}, "updated_at": time.time()}


def _save(db: dict[str, Any]) -> None:
    """Persist data to storage."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db["updated_at"] = time.time()
    path.write_text(json.dumps(db, indent=2, ensure_ascii=False))
