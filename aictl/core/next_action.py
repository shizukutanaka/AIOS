"""Post-command 'what's next' suggestions.

Apple principle: every completed action should suggest the logical next step.
iPhone setup completes → "Set up Apple Pay"
macOS update installs → "Restart to apply"
aictl indexes docs → "aictl rag ask 'your question'"

Usage:
    from aictl.core.next_action import suggest
    suggest("rag_index", docs_count=5)
"""

from __future__ import annotations

from typing import Any



_SUGGESTIONS: dict[str, list[str]] = {
    "eval_run": [
        "aictl eval compare --suite evals.json --baseline baseline.json",
        "aictl eval report  --suite evals.json",
    ],
    "spec_recommend": [
        "aictl spec bench <target> --draft <draft>  # verify speedup estimate",
        "aictl fit <draft-model>                      # check draft fits your GPU",
    ],
    "rag_index": [
        "aictl rag ask 'your question'   # query your documents",
        "aictl rag status                 # see index stats",
    ],
    "rag_ask": [
        "aictl rag search 'query'        # see raw search results",
        "aictl guard scan 'text'         # scan for PII before sharing",
    ],
    "doctor": [
        "aictl recommend                 # see models for your hardware",
        "aictl fit <model>               # check if a specific model fits",
    ],
    "doctor_issues": [
        "aictl troubleshoot              # diagnose problems automatically",
        "aictl setup                     # guided setup from scratch",
    ],
    "fit_success": [
        "aictl quant recommend <model>   # pick best quantization",
        "aictl serve <model>             # start the model",
    ],
    "fit_fail": [
        "aictl recommend                 # see models that DO fit",
        "aictl quant compare <model>     # try a smaller quantization",
    ],
    "guard_clean": [
        "aictl guard scan --file <path>  # scan a file",
        "aictl rag ask 'question'        # query with PII protection",
    ],
    "guard_pii": [
        "aictl guard scan --redact <text>  # redact PII automatically",
    ],
    "cache_hit": [
        "aictl cache status              # see total tokens saved",
    ],
    "setup_done": [
        "aictl chat <model>              # start chatting",
        "aictl rag index ./docs          # index your documents",
        "aictl help                      # explore all features",
    ],
    "first_run": [
        "aictl doctor                    # check your system",
        "aictl recommend                 # find models for your hardware",
        "aictl help getting-started      # full onboarding guide",
    ],
    "troubleshoot_done": [
        "aictl doctor                    # verify fix worked",
        "aictl status                    # see current system state",
    ],
    "update_done": [
        "aictl --version                 # confirm new version",
        "aictl help advanced             # see what's new",
    ],
}


def suggest(key: str, **context: Any) -> None:
    """Print 'Try next:' suggestions after a command completes.

    Args:
        key: Identifies which suggestions to show.
        **context: Optional context (e.g. docs_count, model_name).
    """
    actions = _SUGGESTIONS.get(key, [])
    if not actions:
        return

    print()
    print("  Try next:")
    for action in actions[:2]:  # Max 2 suggestions — Apple never overwhelms
        print(f"    {action}")
    print()


def suggest_after_error(command: str) -> None:
    """Suggest recovery options after a command fails."""
    print()
    print("  Need help?")
    print("    aictl troubleshoot       # automatic diagnosis")
    print("    aictl doctor             # system health check")
    print()
