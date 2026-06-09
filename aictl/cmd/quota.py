"""aictl quota — team-based quota and chargeback.

LiteLLM has virtual keys. We go further: per-team GPU hour quotas
with real chargeback data, no external database required.

  aictl quota create team-eng --tokens-per-month 10000000
  aictl quota list
  aictl quota report
  aictl quota reset team-eng
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
        "quota",
        help="Team-based token quotas and chargeback.",
    )
    sp = p.add_subparsers(dest="quota_cmd", required=True)

    c = sp.add_parser("create", help="Create or update a team quota.")
    c.add_argument("team", help="Team name")
    c.add_argument("--tokens-per-month", type=int, default=10_000_000,
                   help="Monthly token limit")
    c.add_argument("--priority", choices=["high", "normal", "low"],
                   default="normal")
    c.set_defaults(func=run_create)

    sp.add_parser("list", help="List all quotas.").set_defaults(func=run_list)

    sp.add_parser("report", help="Monthly usage report.").set_defaults(func=run_report)

    rst = sp.add_parser("reset", help="Reset a team's usage counter.")
    rst.add_argument("team", help="Team name")
    rst.add_argument("--yes", action="store_true")
    rst.set_defaults(func=run_reset)


def run_create(args: argparse.Namespace) -> int:
    """Create a new entry."""
    db = _load()
    db["teams"][args.team] = {
        "tokens_per_month": args.tokens_per_month,
        "priority": args.priority,
        "created_at": time.time(),
        "used_tokens": db["teams"].get(args.team, {}).get("used_tokens", 0),
    }
    _save(db)
    ok(f"Quota set: {args.team} → {args.tokens_per_month:,} tokens/month "
       f"(priority: {args.priority})")
    return 0


def run_list(args: argparse.Namespace) -> int:
    """List all entries."""
    db = _load()
    if not db["teams"]:
        warn("No quotas defined.")
        print("  Try: aictl quota create my-team --tokens-per-month 10000000")
        return 0

    if getattr(args, "json", False):
        print_json(db["teams"])
        return 0

    print()
    rows = []
    for name, cfg in db["teams"].items():
        used = cfg.get("used_tokens", 0)
        limit = cfg["tokens_per_month"]
        pct = used / limit * 100 if limit > 0 else 0
        rows.append({
            "TEAM": name,
            "USED": f"{used:,}",
            "LIMIT": f"{limit:,}",
            "%": f"{pct:.0f}%",
            "PRIORITY": cfg["priority"],
        })
    print_table(rows, columns=["TEAM", "USED", "LIMIT", "%", "PRIORITY"])
    print()
    return 0


def run_report(args: argparse.Namespace) -> int:
    """Generate a usage report."""
    db = _load()
    if not db["teams"]:
        warn("No quotas defined.")
        return 0

    if getattr(args, "json", False):
        report = {
            name: {
                "used_tokens": cfg.get("used_tokens", 0),
                "limit_tokens": cfg["tokens_per_month"],
                "utilization_pct": (round(
                    cfg.get("used_tokens", 0) / cfg["tokens_per_month"] * 100, 1
                ) if cfg["tokens_per_month"] > 0 else 0),
                "priority": cfg["priority"],
            }
            for name, cfg in db["teams"].items()
        }
        print_json(report)
        return 0

    print()
    print(f"  Quota Report — {time.strftime('%Y-%m')}")
    print()
    total_used = 0
    total_limit = 0
    for name, cfg in db["teams"].items():
        used = cfg.get("used_tokens", 0)
        limit = cfg["tokens_per_month"]
        pct = used / limit * 100 if limit > 0 else 0
        total_used += used
        total_limit += limit
        icon = "⚠" if pct > 80 else "✓"
        # Rough cost estimate: ¥0.75/1K tokens local inference equivalent
        cost_jpy = used / 1000 * 0.75
        print(f"  {icon} {name:<20} {used:>12,} / {limit:>12,} tokens "
              f"({pct:>5.1f}%)  ≈ ¥{cost_jpy:,.0f}")

    print()
    total_pct = total_used / total_limit * 100 if total_limit > 0 else 0
    print(f"  {'TOTAL':<20} {total_used:>12,} / {total_limit:>12,} tokens "
          f"({total_pct:>5.1f}%)")
    print()
    return 0


def run_reset(args: argparse.Namespace) -> int:
    """Reset to empty state."""
    db = _load()
    if args.team not in db["teams"]:
        err(f"Unknown team: {args.team}")
        return 1
    if not getattr(args, "yes", False):
        warn(f"This will reset {args.team}'s token counter to 0.")
        print("  Re-run with --yes to confirm.")
        return 1
    db["teams"][args.team]["used_tokens"] = 0
    _save(db)
    ok(f"{args.team} usage counter reset.")
    return 0


def _db_path() -> Path:
    """Execute db path."""
    base = os.environ.get("AIOS_STATE_DIR", os.path.expanduser("~/.aios"))
    return Path(base) / "quotas.json"


def _load() -> dict[str, Any]:
    """Load and return data from storage."""
    path = _db_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass  # best-effort; failure is non-critical
    return {"teams": {}, "updated_at": time.time()}


def _save(db: dict[str, Any]) -> None:
    """Persist data to storage."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db["updated_at"] = time.time()
    path.write_text(json.dumps(db, indent=2, ensure_ascii=False))
