"""Model cache management: track disk usage and clean up stale models.

Default cache locations:
  - Ollama:  ~/.ollama/models/
  - HF Hub:  ~/.cache/huggingface/hub/
  - Custom:  ~/.aios/models/
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CacheEntry:
    path: str
    name: str
    size_bytes: int
    last_accessed: float
    source: str  # ollama | huggingface | custom


@dataclass
class CacheReport:
    total_bytes: int = 0
    entries: list[CacheEntry] = field(default_factory=list)
    locations: dict[str, int] = field(default_factory=dict)  # source → bytes


CACHE_LOCATIONS = {
    "ollama": Path.home() / ".ollama" / "models",
    "huggingface": Path.home() / ".cache" / "huggingface" / "hub",
    "custom": Path.home() / ".aios" / "models",
}


def scan_cache(extra_dirs: list[Path] | None = None) -> CacheReport:
    """Scan all known model cache locations."""
    report = CacheReport()
    dirs = dict(CACHE_LOCATIONS)
    if extra_dirs:
        for i, d in enumerate(extra_dirs):
            dirs[f"extra_{i}"] = d

    for source, base_path in dirs.items():
        if not base_path.exists():
            continue

        source_total = 0
        for root, _dirs, files in os.walk(base_path):
            for f in files:
                fp = Path(root) / f
                try:
                    stat = fp.stat()
                    size = stat.st_size
                    atime = stat.st_atime
                    source_total += size

                    # Only track large files (>1MB) as model entries
                    if size > 1_048_576:
                        report.entries.append(CacheEntry(
                            path=str(fp),
                            name=fp.name,
                            size_bytes=size,
                            last_accessed=atime,
                            source=source,
                        ))
                except OSError:
                    pass  # best-effort; failure is non-critical

        report.locations[source] = source_total
        report.total_bytes += source_total

    # Sort by size descending
    report.entries.sort(key=lambda e: e.size_bytes, reverse=True)
    return report


def find_stale(report: CacheReport, days: int = 30) -> list[CacheEntry]:
    """Find cache entries not accessed in N days."""
    cutoff = time.time() - (days * 86400)
    return [e for e in report.entries if e.last_accessed < cutoff]


def clean_stale(report: CacheReport, days: int = 30, dry_run: bool = True) -> list[CacheEntry]:
    """Remove stale cache entries. Returns list of removed entries."""
    stale = find_stale(report, days)
    removed: list[CacheEntry] = []

    for entry in stale:
        if dry_run:
            removed.append(entry)
        else:
            try:
                os.remove(entry.path)
                removed.append(entry)
            except OSError:
                pass  # best-effort; failure is non-critical

    return removed


def format_bytes(b: int) -> str:
    """Human-readable byte size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024  # type: ignore
    return f"{b:.1f} PB"
