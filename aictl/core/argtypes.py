"""Reusable argparse ``type=`` callables for input validation at parse time.

Idiomatic argparse validation puts the check in a ``type=`` conversion function
that raises ``argparse.ArgumentTypeError`` — the parser then rejects bad input
*before* the handler runs, with argparse's standard usage error (exit 2) and a
clean one-line message. This is the canonical pattern recommended across the
Python community (see docs/INPUT_VALIDATION_SPEC.md §5, improvement #2) and
makes the V1 invariant (physical quantities >= their minimum) a reusable *type*
instead of per-handler boilerplate.

Handlers keep their own guards as defense-in-depth for programmatic/SDK callers
that construct a Namespace directly and never pass through the parser.

  p.add_argument("--top", type=positive_int, default=5)
  p.add_argument("--per-day", type=nonneg_int, default=0)   # 0 = unlimited
"""

from __future__ import annotations

import argparse


def positive_int(value: str) -> int:
    """argparse type: a strictly positive integer (>= 1).

    For counts and physical sizes (--top, -n, -k, --context, --vram, ...): a
    value < 1 is meaningless and risks the negative-slice / negative-math traps.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}")
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {n}")
    return n


def nonneg_int(value: str) -> int:
    """argparse type: a non-negative integer (>= 0).

    For quantities where 0 is a meaningful sentinel (e.g. a token quota where
    0 = unlimited): only a negative value is invalid.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}")
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {n}")
    return n
