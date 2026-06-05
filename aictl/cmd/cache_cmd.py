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
