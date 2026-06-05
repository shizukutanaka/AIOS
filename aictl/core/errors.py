"""aictl error system — designed for humans, not stack traces.

Apple principle: every error answers three questions in one short message:
  1. What happened? (plain language)
  2. Why? (only if it helps the user)
  3. What do I do now? (exactly one suggested action)

This module is imported by __main__'s error handler and by any command
that wants to raise a user-friendly exception.
"""

from __future__ import annotations

from typing import Any

import sys


class AictlError(Exception):
    """Base class for every user-facing error.

    Subclasses define three strings:
      user_message:      What went wrong, in plain language.
      suggested_action:  Exactly one next command to run.
      why:               Optional explanation (omit if user doesn't need it).
    """

    user_message: str = "Something went wrong."
    suggested_action: str = ""
    why: str = ""
    exit_code: int = 1

    def __str__(self) -> str:
        """Return the string representation."""
        parts = [f"  {self.user_message}"]
        if self.why:
            parts.append(f"\n  Why: {self.why}")
        if self.suggested_action:
            parts.append(f"\n  Try this:\n    {self.suggested_action}")
        return "\n".join(parts)


class NoEngineAvailable(AictlError):
    user_message = "No inference engine is running yet."
    suggested_action = (
        "aictl setup            # install Ollama or vLLM with sensible defaults"
    )
    exit_code = 2


class ModelTooLarge(AictlError):
    exit_code = 2

    def __init__(self, model: str, gpu: str, need_gb: float, have_gb: float) -> None:
        """Initialize the instance with provided arguments."""
        super().__init__()
        gap = need_gb - have_gb
        self.user_message = (
            f"{model} needs about {need_gb:.0f}GB of GPU memory, "
            f"but {gpu} has only {have_gb:.0f}GB."
        )
        self.why = f"Gap is {gap:.0f}GB. Either shrink the model or pick another."
        self.suggested_action = (
            f"aictl recommend        # see models that fit your {gpu}"
        )


class OutOfMemory(AictlError):
    user_message = "Your GPU ran out of memory."
    why = "The model + KV cache + activations exceeded available VRAM."
    suggested_action = (
        "aictl doctor           # find the specific cause and one-line fix"
    )
    exit_code = 2


class ModelNotFound(AictlError):
    exit_code = 2

    def __init__(self, name: str) -> None:
        """Initialize the instance with provided arguments."""
        super().__init__()
        self.user_message = f"No model matches '{name}'."
        self.suggested_action = "aictl recommend        # see the full model catalog"


class NetworkUnavailable(AictlError):
    user_message = "Cannot reach the network right now."
    why = "Cloud fallback, model downloads, and health checks need internet."
    suggested_action = "aictl status          # see what still works locally"
    exit_code = 3


class CostBudgetExceeded(AictlError):
    exit_code = 2

    def __init__(self, budget_usd: float, current_usd: float) -> None:
        """Initialize the instance with provided arguments."""
        super().__init__()
        self.user_message = (
            f"Monthly spending limit of ${budget_usd:.2f} reached "
            f"(${current_usd:.2f} used)."
        )
        self.suggested_action = (
            "aictl cost analyze    # see what's consuming the most budget"
        )


class MissingDependency(AictlError):
    exit_code = 2

    def __init__(self, what: str, install_cmd: str) -> None:
        """Initialize the instance with provided arguments."""
        super().__init__()
        self.user_message = f"{what} is not installed."
        self.suggested_action = install_cmd


class PermissionDenied(AictlError):
    exit_code = 2

    def __init__(self, path: str) -> None:
        """Initialize the instance with provided arguments."""
        super().__init__()
        self.user_message = f"Cannot write to {path}."
        self.why = "aictl needs this location to store state and logs."
        self.suggested_action = (
            f"sudo chown -R $USER {path}    # or set AIOS_STATE_DIR to another path"
        )


def format_for_user(e: BaseException) -> str:
    """Turn any exception into a human-readable message.

    Used by __main__ to catch exceptions that bubble out of commands.
    """
    if isinstance(e, AictlError):
        return str(e)

    # Common Python exceptions → friendly messages
    if isinstance(e, FileNotFoundError):
        return (
            f"  File not found: {e.filename or e}\n"
            "\n  Try this:\n"
            "    aictl doctor       # verify your configuration"
        )
    if isinstance(e, PermissionError):
        return (
            f"  Permission denied: {getattr(e, 'filename', '?')}\n"
            "\n  Try this:\n"
            "    Check file ownership, or run with AIOS_STATE_DIR=/tmp/aios"
        )
    if isinstance(e, ConnectionRefusedError):
        return (
            "  Cannot connect to the inference engine.\n"
            "\n  Why: Nothing is listening on the expected port.\n"
            "\n  Try this:\n"
            "    aictl status       # see what's running"
        )
    if isinstance(e, TimeoutError):
        return (
            "  Operation timed out.\n"
            "\n  Try this:\n"
            "    aictl doctor       # identify the slow component"
        )

    # Generic fallback — show the type, hide the traceback
    return (
        f"  Unexpected error: {type(e).__name__}: {e}\n"
        "\n  Try this:\n"
        "    aictl doctor       # full system check\n"
        "\n  If the problem persists, report at:\n"
        "    https://github.com/shizukutanaka/aios/issues/new\n"
        "    (include the output of `aictl doctor --json`)"
    )


def print_error(e: BaseException, out: Any=None) -> None:
    """Print an error to stderr with consistent formatting."""
    if out is None:
        out = sys.stderr
    print(format_for_user(e), file=out)
