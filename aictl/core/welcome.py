"""aictl welcome — the first impression.

Apple principle: the first 30 seconds decide everything. When a user
types `aictl` with no args, we must not show a wall of 48 commands.
We show a warm greeting, detect their environment, and suggest exactly
one next action.
"""

from __future__ import annotations

from typing import Any

import os
import sys
from pathlib import Path

from aictl.core.constants import AICTL_VERSION


WELCOME_BANNER = f"""
  ┌─────────────────────────────────────────┐
  │  aictl {AICTL_VERSION:<33}│
  │  AI infrastructure, local-first.        │
  └─────────────────────────────────────────┘
"""


def is_first_run() -> bool:
    """True if the user has never successfully used aictl before."""
    state_dir = Path(os.environ.get("AIOS_STATE_DIR", Path.home() / ".aios"))
    marker = state_dir / ".welcome_shown"
    return not marker.exists()


def mark_welcome_shown() -> None:
    """Remember that we've shown the welcome screen."""
    state_dir = Path(os.environ.get("AIOS_STATE_DIR", Path.home() / ".aios"))
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / ".welcome_shown").touch()
    except OSError:
        pass  # Don't fail if state dir is read-only


def show_welcome() -> int:
    """Show the welcome screen when someone runs `aictl` with no args."""
    print(WELCOME_BANNER)

    first_time = is_first_run()
    next_action = _detect_next_action(first_time)

    if first_time:
        print("  Welcome to aictl. Let's get you started.")
        print()
    else:
        print("  Ready when you are.")
        print()

    print(f"  → {next_action['cmd']}")
    print(f"    {next_action['why']}")
    print()

    # Context-sensitive command list
    _show_contextual_commands()

    print("  Full guide: aictl help")
    print()

    mark_welcome_shown()
    return 0


def _show_contextual_commands() -> None:
    """Show the most relevant commands based on current state."""
    state_dir = Path(os.environ.get("AIOS_STATE_DIR", Path.home() / ".aios"))
    initialized = (state_dir / "node.json").exists()

    # Check RAG index
    has_rag = False
    try:
        from aictl.core.rag import RagStore
        has_rag = RagStore().stats()["documents"] > 0
    except Exception:
        pass  # silent — progressive detection fallthrough

    print("  Quick actions:")
    if not initialized:
        print("    aictl setup          Guided 5-step setup")
        print("    aictl doctor         Check hardware and dependencies")
    elif has_rag:
        print("    aictl rag ask '...'  Query your indexed documents")
        print("    aictl dash           Full system dashboard")
        print("    aictl tco            Your real AI cost this month")
    else:
        print("    aictl recommend      Models that fit your hardware")
        print("    aictl rag index ./   Index documents for AI search")
        print("    aictl dash           Full system dashboard")
    print()


def _detect_next_action(first_time: bool) -> dict[str, Any]:
    """Pick the single best next action based on current system state.

    Apple principle: the suggestion must be the MOST USEFUL thing right now,
    not a generic fallback. Read the actual state to decide.
    """
    state_dir = Path(os.environ.get("AIOS_STATE_DIR", Path.home() / ".aios"))

    # ── Level 0: Never used before ──────────────────────
    if first_time or not state_dir.exists():
        return {
            "cmd": "aictl setup",
            "why": "Guided 5-step setup. Takes about 5 minutes.",
        }

    # ── Level 1: System not initialized ─────────────────
    if not (state_dir / "node.json").exists():
        return {
            "cmd": "aictl setup --non-interactive",
            "why": "Quick auto-setup with sensible defaults.",
        }

    # ── Level 2: No RAG index yet ────────────────────────
    try:
        from aictl.core.rag import RagStore
        rag_stats = RagStore().stats()
        if rag_stats["documents"] == 0:
            # Check if there are obvious docs directories nearby
            candidates = [Path.home() / "Documents", Path("./docs"), Path("./README.md")]
            found = next((p for p in candidates if p.exists()), None)
            if found:
                return {
                    "cmd": f"aictl rag index {found}",
                    "why": "Index your documents for AI-powered search.",
                }
    except Exception:
        pass  # silent — progressive detection fallthrough

    # ── Level 3: Cache hit rate is low ──────────────────
    try:
        from aictl.core.sem_cache import get_default_cache
        stats = get_default_cache().stats()
        if stats["entries"] == 0 and stats["session_misses"] > 3:
            return {
                "cmd": "aictl cache status",
                "why": "Semantic cache is empty — queries cost more than needed.",
            }
    except Exception:
        pass  # silent — progressive detection fallthrough

    # ── Level 4: Quota approaching limit ─────────────────
    try:
        import json
        quota_path = state_dir / "quotas.json"
        if quota_path.exists():
            qdata = json.loads(quota_path.read_text())
            for team, cfg in qdata.get("teams", {}).items():
                used = cfg.get("used_tokens", 0)
                limit = cfg.get("tokens_per_month", 1)
                if limit > 0 and used / limit > 0.8:
                    return {
                        "cmd": "aictl quota report",
                        "why": f"{team} quota is over 80% — review before month end.",
                    }
    except Exception:
        pass  # silent — progressive detection fallthrough

    # ── Level 5: No models running ───────────────────────
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "aictl", "ps", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and "[]" in result.stdout:
            return {
                "cmd": "aictl recommend",
                "why": "No models loaded. Pick one for your hardware.",
            }
    except Exception:
        pass  # silent — progressive detection fallthrough

    # ── Level 6: System is healthy, suggest dashboard ───
    return {
        "cmd": "aictl dash",
        "why": "Full system overview — health, cache, cost, perf.",
    }
