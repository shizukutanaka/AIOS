"""Terminal output with visual hierarchy — consistent across all commands.

Apple HIG for CLI:
  Primary text:    Full brightness — the answer
  Secondary text:  Dimmed — supporting info
  Success:         Green ✓
  Warning:         Yellow ⚠
  Error:           Red ✗
  Progress:        Inline spinner or bar

All color codes degrade gracefully when terminal doesn't support ANSI.
"""

from __future__ import annotations

from typing import Any

import os
import sys
import threading
import time


def _supports_color() -> bool:
    """True if the terminal supports ANSI color codes."""
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    if term == "dumb":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if sys.platform == "win32":
        return False
    return True


_COLOR = _supports_color()


# ── ANSI codes ─────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    """Wrap text in ANSI color code if supported."""
    if not _COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def primary(text: str) -> str:
    """Bright white — the main answer."""
    return _c("1", text)  # bold


def secondary(text: str) -> str:
    """Dimmed — supporting information."""
    return _c("2", text)  # dim


def success(text: str) -> str:
    """Return ANSI-colored success text (green)."""
    return _c("32", text)   # green


def warning(text: str) -> str:
    """Return ANSI-colored warning text (yellow)."""
    return _c("33", text)   # yellow


def error_text(text: str) -> str:
    """Return ANSI-colored error text (red)."""
    return _c("31", text)   # red


def highlight(text: str) -> str:
    """Return ANSI-colored highlighted text (cyan)."""
    return _c("36", text)   # cyan


# ── Spinner ─────────────────────────────────────────────────

class Spinner:
    """Inline progress spinner for long operations.

    Usage:
        with Spinner("Downloading...") as sp:
            time.sleep(3)
            sp.message = "Processing..."
    """

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "", stream: Any=None):
        """Initialize the instance with provided arguments."""
        self.message = message
        self._stream = stream or sys.stdout
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active = _COLOR and self._stream.isatty()

    def __enter__(self) -> "Spinner":
        """Enter the context manager."""
        if self._active:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            print(f"  {self.message}", file=self._stream, flush=True)
        return self

    def __exit__(self, *_: Any) -> None:
        """Exit the context manager."""
        self._stop.set()
        if self._thread:
            self._thread.join()
        if self._active:
            # Clear the spinner line
            self._stream.write("\r\033[K")
            self._stream.flush()

    def _spin(self) -> None:
        """Execute spin."""
        i = 0
        while not self._stop.is_set():
            frame = self._FRAMES[i % len(self._FRAMES)]
            self._stream.write(f"\r  {frame} {self.message}")
            self._stream.flush()
            time.sleep(0.08)
            i += 1


# ── Progress bar ────────────────────────────────────────────

def progress_bar(current: int, total: int, width: int = 30, label: str = "") -> str:
    """Return a text progress bar string.

    Example: [████████░░░░░░░] 53%
    """
    if total <= 0:
        return ""
    pct = min(1.0, current / total)
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    pct_str = f"{pct * 100:.0f}%"
    result = f"[{bar}] {pct_str}"
    if label:
        result = f"{label}  {result}"
    if _COLOR:
        result = f"\033[36m{result}\033[0m"
    return result


def print_progress(current: int, total: int, label: str = "") -> None:
    """Print a progress bar, overwriting the previous line."""
    bar = progress_bar(current, total, label=label)
    if sys.stdout.isatty():
        sys.stdout.write(f"\r  {bar}")
        sys.stdout.flush()
        if current >= total:
            sys.stdout.write("\n")
            sys.stdout.flush()
    else:
        if current == total:
            print(f"  {bar}")


# ── Section header ───────────────────────────────────────────

def section(title: str) -> None:
    """Print a section header in Apple style."""
    line = f"── {title} "
    if _COLOR:
        print(f"\n  \033[2m{line}\033[0m")
    else:
        print(f"\n  {line}")


# ── Key-value pairs with visual hierarchy ────────────────────

def kv_row(key: str, value: str, key_width: int = 16) -> None:
    """Print a key=value row with dimmed key and bright value."""
    key_str = key.ljust(key_width)
    if _COLOR:
        print(f"  \033[2m{key_str}\033[0m  {value}")
    else:
        print(f"  {key_str}  {value}")
