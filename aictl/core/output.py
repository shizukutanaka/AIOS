"""Output formatting for aictl."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from typing import Any


def _to_dict(obj: Any) -> Any:
    """Serialize to a dictionary."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, list):
        return [_to_dict(o) for o in obj]
    return obj


def print_json(data: Any) -> None:
    """Print json."""
    print(json.dumps(_to_dict(data), indent=2, default=str))


def print_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    """Print table."""
    if not rows:
        print("(no data)")
        return
    if columns is None:
        columns = list(rows[0].keys())

    widths = {c: len(c) for c in columns}
    for r in rows:
        for c in columns:
            widths[c] = max(widths[c], len(str(r.get(c, ""))))

    header = "  ".join(c.upper().ljust(widths[c]) for c in columns)
    print(header)
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        line = "  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns)
        print(line)


def print_kv(pairs: list[tuple[str, Any]], indent: int = 0) -> None:
    """Print kv."""
    if not pairs:
        return
    max_key = max(len(k) for k, _ in pairs)
    prefix = " " * indent
    for k, v in pairs:
        print(f"{prefix}{k.ljust(max_key)}  {v}")


def ok(msg: str) -> None:
    """Ok."""
    print(f"✓ {msg}")


def warn(msg: str) -> None:
    """Warn."""
    print(f"⚠ {msg}", file=sys.stderr)


def err(msg: str) -> None:
    """Err."""
    print(f"✗ {msg}", file=sys.stderr)
