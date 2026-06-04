"""Structured logging: JSON log output for all aictl operations.

Provides:
  - JSON Lines log format (one JSON object per line)
  - Automatic context enrichment (timestamp, node_id, command, version)
  - Log levels: debug, info, warn, error
  - File rotation (daily, max 7 files)
  - Correlation IDs for request tracing

Usage:
  from aictl.core.logging import get_logger
  log = get_logger("aictl.cmd.deploy")
  log.info("Deploying model", model="llama3", gpus=2)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LogEntry:
    timestamp: str = ""
    level: str = "info"
    logger: str = ""
    message: str = ""
    node_id: str = ""
    version: str = ""
    correlation_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Set defaults for log entry."""
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    def to_json(self) -> str:
        """To json."""
        d = {
            "ts": self.timestamp,
            "level": self.level,
            "logger": self.logger,
            "msg": self.message,
        }
        if self.node_id:
            d["node_id"] = self.node_id
        if self.correlation_id:
            d["correlation_id"] = self.correlation_id
        if self.extra:
            d.update(self.extra)
        return json.dumps(d, separators=(",", ":"))


class StructuredLogger:
    """JSON Lines logger with file rotation."""

    def __init__(self, name: str, log_dir: Path | None = None,
                 level: str = "info"):
        """Initialize structured logger."""
        self.name = name
        self.level = level
        self._levels = {"debug": 0, "info": 1, "warn": 2, "error": 3}

        if log_dir is None:
            from aictl.core.state import DEFAULT_STATE_DIR
            log_dir = DEFAULT_STATE_DIR / "logs"
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._node_id = ""
        self._correlation_id = ""

    def set_context(self, node_id: str = "", correlation_id: str = "") -> None:
        """Set context."""
        if node_id:
            self._node_id = node_id
        if correlation_id:
            self._correlation_id = correlation_id

    def debug(self, msg: str, **kwargs: Any) -> None:
        """Debug."""
        self._log("debug", msg, kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        """Info."""
        self._log("info", msg, kwargs)

    def warn(self, msg: str, **kwargs: Any) -> None:
        """Warn."""
        self._log("warn", msg, kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        """Error."""
        self._log("error", msg, kwargs)

    def _log(self, level: str, msg: str, extra: dict[str, Any]) -> None:
        """Execute log."""
        if self._levels.get(level, 0) < self._levels.get(self.level, 0):
            return

        entry = LogEntry(
            level=level,
            logger=self.name,
            message=msg,
            node_id=self._node_id,
            correlation_id=self._correlation_id,
            extra=extra,
        )

        line = entry.to_json() + "\n"

        # Write to daily log file
        date = time.strftime("%Y-%m-%d")
        log_file = self.log_dir / f"aictl-{date}.jsonl"
        try:
            with open(log_file, "a") as f:
                f.write(line)
        except OSError:
            pass  # best-effort; failure is non-critical

    def read_logs(self, n: int = 50, level: str = "") -> list[dict[str, Any]]:
        """Read recent log entries."""
        entries: list[dict[str, Any]] = []
        for f in sorted(self.log_dir.glob("aictl-*.jsonl"), reverse=True):
            try:
                lines = f.read_text().strip().splitlines()
                for line in reversed(lines):
                    try:
                        entry = json.loads(line)
                        if level and entry.get("level") != level:
                            continue
                        entries.append(entry)
                        if len(entries) >= n:
                            return entries
                    except json.JSONDecodeError:
                        continue
            except OSError:
                continue
        return entries

    def rotate(self, max_days: int = 7) -> int:
        """Remove log files older than max_days. Returns count removed."""
        cutoff = time.time() - (max_days * 86400)
        removed = 0
        for f in self.log_dir.glob("aictl-*.jsonl"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        return removed


# Global logger cache
_loggers: dict[str, StructuredLogger] = {}


def get_logger(name: str = "aictl") -> StructuredLogger:
    """Get logger."""
    if name not in _loggers:
        level = os.environ.get("AIOS_LOG_LEVEL", "info")
        _loggers[name] = StructuredLogger(name, level=level)
    return _loggers[name]
