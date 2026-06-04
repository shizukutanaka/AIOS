"""Consistent empty state messages.

Apple HIG: Empty states are opportunities, not failures.
Every empty state should:
  1. Explain what this feature does (for new users)
  2. Give exactly one action to start
  3. Never just say "nothing here"

Usage:
    from aictl.core.empty_state import show
    show("rag_index")
"""

from __future__ import annotations

from typing import Any

import sys


_EMPTY_STATES: dict[str, dict[str, Any]] = {
    "rag_index": {
        "title": "No documents indexed yet.",
        "description": "aictl rag lets you ask questions about your own files.",
        "action": "aictl rag index ./docs",
        "action_label": "Index a folder of documents",
    },
    "quota": {
        "title": "No team quotas defined.",
        "description": "Quotas let you allocate GPU time and track costs per team.",
        "action": "aictl quota create my-team --tokens-per-month 10000000",
        "action_label": "Create your first quota",
    },
    "batch": {
        "title": "No batch jobs scheduled.",
        "description": "Batch jobs run during GPU idle time — embed docs, run evals, classify files.",
        "action": "aictl batch add myjob --schedule '0 2 * * *' --input ./docs",
        "action_label": "Schedule a nightly job",
    },
    "perf": {
        "title": "No performance data recorded yet.",
        "description": "Every aictl command is automatically timed.",
        "action": "aictl status",
        "action_label": "Run a command to generate data",
    },
    "cache": {
        "title": "Semantic cache is empty.",
        "description": "The cache returns stored responses for similar prompts — saving inference cost.",
        "action": "import aictl; aictl.ai.ask('your question')",
        "action_label": "Make a few API calls to populate the cache",
    },
    "guard_scan": {
        "title": "Nothing to scan.",
        "description": "aictl guard detects PII and policy violations locally.",
        "action": "aictl guard scan 'your text here'",
        "action_label": "Scan some text",
    },
    "tco": {
        "title": "No cost data yet.",
        "description": "aictl tco shows your real AI cost: electricity + hardware depreciation.",
        "action": "aictl tco setup",
        "action_label": "Configure your GPU price and electricity rate",
    },
}


def show(key: str, out: Any=None) -> None:
    """Print an empty state message.

    Args:
        key: Identifies which empty state to show.
        out: Output stream (default: stdout).
    """
    if out is None:
        out = sys.stdout

    state = _EMPTY_STATES.get(key)
    if not state:
        return

    print(f"\n  {state['title']}", file=out)
    print(f"  {state['description']}", file=out)
    print(file=out)
    print("  Get started:", file=out)
    print(f"    {state['action']}", file=out)
    print(f"  ↑ {state['action_label']}", file=out)
    print(file=out)


def is_empty(key: str) -> bool:
    """True if this key has an empty state definition."""
    return key in _EMPTY_STATES
