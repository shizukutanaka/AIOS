"""aictl cache — manage the semantic response cache.

The semantic cache saves inference cost by returning stored responses
for similar (not just identical) prompts.

  aictl cache status     Show hit rate and savings
  aictl cache clear      Wipe all cached responses
  aictl cache disable    Turn off caching for this session
"""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, warn, print_json


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "cache",
        help="Manage the semantic response cache.",
    )
    sp = p.add_subparsers(dest="cache_cmd")
    sp.required = True

    sp.add_parser("status", help="Show cache hit rate and token savings.").set_defaults(func=run_status)

    clr = sp.add_parser("clear", help="Wipe all cached responses.")
    clr.add_argument("--yes", action="store_true", help="Skip confirmation.")
    clr.set_defaults(func=run_clear)

    sp.add_parser("disable", help="Disable caching for this session.").set_defaults(func=run_disable)

    warm = sp.add_parser(
        "warm",
        help="Pre-seed cache from a JSONL file of Q&A pairs.",
        description=(
            "Reads a JSONL file (one JSON object per line) with keys "
            "'prompt', 'response', 'model' (required) and 'tokens' (optional). "
            "Inserts each pair into the semantic cache so future similar queries "
            "will match without calling the inference engine."
        ),
    )
    warm.add_argument("file", help="JSONL file with Q&A pairs to seed.")
    warm.add_argument("--dry-run", action="store_true",
                      help="Parse and validate without writing to cache.")
    warm.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    warm.set_defaults(func=run_warm)


def run_status(args: argparse.Namespace) -> int:
    """Show current status."""
    from aictl.core.sem_cache import get_default_cache

    cache = get_default_cache()
    stats = cache.stats()

    if getattr(args, "json", False):
        print_json(stats)
        return 0

    print()
    print("  Semantic Cache")
    print()

    if stats["entries"] == 0:
        print("  Empty — no responses cached yet.")
        print()
        print("  The cache fills automatically as you use aictl.")
        print("  Second call with a similar prompt returns instantly at $0 cost.")
        print()
        return 0

    hit_pct = stats["session_hit_rate"] * 100
    icon = "✓" if hit_pct >= 30 else "○"
    print(f"  {icon} Hit rate:       {hit_pct:.1f}%  "
          f"({stats['session_hits']} hits / "
          f"{stats['session_hits'] + stats['session_misses']} requests)")
    print(f"  ○ Entries:        {stats['entries']:,}")
    print(f"  ○ Tokens saved:   {stats['total_tokens_saved']:,}")
    print(f"  ○ Lifetime hits:  {stats['lifetime_hits']:,}")
    print(f"  ○ Threshold:      {stats['threshold']} cosine similarity")
    print(f"  ○ DB:             {stats['db_path']}")
    print()

    if hit_pct < 10:
        print("  Tip: Hit rate is low. This is normal for diverse workloads.")
        print("       RAG queries and repeated system prompts benefit most.")
    elif hit_pct >= 40:
        ok("  Great hit rate! Saving significant inference cost.")
    print()
    return 0


def run_clear(args: argparse.Namespace) -> int:
    """Clear all stored data."""
    if not getattr(args, "yes", False):
        warn("This will delete all cached responses.")
        print("  Re-run with --yes to confirm.")
        return 1

    from aictl.core.sem_cache import get_default_cache
    get_default_cache().clear()
    ok("Cache cleared.")
    return 0


def run_disable(args: argparse.Namespace) -> int:
    # We implement this by setting a process-wide flag in the sem_cache module
    """Disable feature for this session."""
    import aictl.core.sem_cache as _mod
    _mod._DISABLED = True
    ok("Semantic cache disabled for this session.")
    print("  (Restart to re-enable)")
    return 0


def run_warm(args: argparse.Namespace) -> int:
    """Pre-seed the semantic cache from a JSONL file.

    Each line must be a JSON object with:
      - prompt   (str, required)
      - response (str, required)
      - model    (str, required)
      - tokens   (int, optional — used for savings accounting)
    """
    import json
    from pathlib import Path
    from aictl.core.output import err

    path = Path(args.file)
    if not path.exists():
        err(f"File not found: {path}")
        return 1

    use_json = getattr(args, "json", False)
    dry_run = getattr(args, "dry_run", False)

    # Parse JSONL
    entries: list[dict] = []
    errors: list[str] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {i}: invalid JSON — {e}")
            continue
        missing = [k for k in ("prompt", "response", "model") if k not in obj]
        if missing:
            errors.append(f"line {i}: missing fields {missing}")
            continue
        entries.append(obj)

    if errors:
        for e_msg in errors[:5]:
            warn(e_msg)
        if len(errors) > 5:
            warn(f"  ... and {len(errors) - 5} more errors")

    if not entries:
        err("No valid entries found.")
        return 1

    if dry_run:
        if use_json:
            print_json({"valid": len(entries), "errors": len(errors), "dry_run": True})
        else:
            ok(f"Dry run: {len(entries)} valid entries, {len(errors)} errors")
        return 0

    # Store into cache
    from aictl.core.sem_cache import get_default_cache
    cache = get_default_cache()
    stored = 0
    skipped = 0
    for obj in entries:
        try:
            cache.store(
                prompt=str(obj["prompt"]),
                response=str(obj["response"]),
                model=str(obj["model"]),
                tokens=int(obj.get("tokens", 0)),
            )
            stored += 1
        except Exception:
            skipped += 1

    if use_json:
        print_json({
            "stored": stored,
            "skipped": skipped,
            "parse_errors": len(errors),
        })
    else:
        ok(f"Cache warmed: {stored} entries stored ({skipped} skipped, {len(errors)} parse errors)")
    return 0
