"""Audit log: structured, append-only log for security and compliance.

Records security-relevant events:
  - Model loading/unloading (with digest)
  - Stack apply/down
  - Node join/leave
  - Trust policy violations
  - API key usage
  - Configuration changes
  - Snapshot create/restore

Format: JSON lines (one JSON object per line) in ~/.aios/audit.jsonl
Rotation: new file per day or when size exceeds 10MB
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aictl.core.state import DEFAULT_STATE_DIR


@dataclass
class AuditEntry:
    timestamp: float = 0.0
    event: str = ""          # model.loaded | stack.applied | node.joined | trust.violation | ...
    actor: str = ""          # user | system | daemon
    resource: str = ""       # model name | stack name | node ID
    action: str = ""         # create | delete | verify | reject
    outcome: str = ""        # success | failure | warning
    details: dict[str, Any] = field(default_factory=dict)
    node_id: str = ""

    def __post_init__(self) -> None:
        """Set defaults for audit entry."""
        if self.timestamp == 0:
            self.timestamp = time.time()


class AuditLog:
    """Append-only audit log."""

    MAX_SIZE = 10 * 1024 * 1024  # 10MB per file

    def __init__(self, state_dir: Path | None = None):
        """Initialize audit log."""
        self.dir = (state_dir or DEFAULT_STATE_DIR) / "audit"
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, entry: AuditEntry) -> None:
        """Write."""
        path = self._current_file()
        line = json.dumps(asdict(entry), default=str) + "\n"

        with open(path, "a") as f:
            f.write(line)

    def read(self, n: int = 50, event_filter: str = "") -> list[AuditEntry]:
        """Read the last N entries, optionally filtered by event type."""
        entries: list[AuditEntry] = []
        for path in sorted(self.dir.glob("audit-*.jsonl"), reverse=True):
            try:
                lines = path.read_text().strip().splitlines()
                for line in reversed(lines):
                    try:
                        data = json.loads(line)
                        if event_filter and data.get("event", "") != event_filter:
                            continue
                        entries.append(AuditEntry(**{
                            k: v for k, v in data.items()
                            if k in AuditEntry.__dataclass_fields__
                        }))
                        if len(entries) >= n:
                            return entries
                    except (json.JSONDecodeError, TypeError):
                        pass  # best-effort; failure is non-critical
            except OSError:
                pass  # best-effort; failure is non-critical
        return entries

    def _current_file(self) -> Path:
        """Execute current file."""
        date = time.strftime("%Y-%m-%d")
        path = self.dir / f"audit-{date}.jsonl"

        # Rotate if too large
        if path.exists() and path.stat().st_size > self.MAX_SIZE:
            ts = int(time.time())
            path = self.dir / f"audit-{date}-{ts}.jsonl"

        return path


# Convenience functions
_log: AuditLog | None = None


def get_audit_log(state_dir: Path | None = None) -> AuditLog:
    """Get audit log."""
    global _log
    if _log is None or (state_dir is not None and _log.dir.parent != state_dir):
        _log = AuditLog(state_dir)
    return _log


def audit(event: str, resource: str = "", action: str = "",
          outcome: str = "success", actor: str = "system",
          state_dir: Path | None = None, **details: Any) -> None:
    """Write an audit entry."""
    log = get_audit_log(state_dir)
    log.write(AuditEntry(
        event=event, resource=resource, action=action,
        outcome=outcome, actor=actor, details=details,
    ))
