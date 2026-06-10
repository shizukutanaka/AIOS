"""API key management and rate limiting for the completions proxy.

Supports:
  - API key generation and validation
  - Per-key rate limiting (requests/min, tokens/min)
  - Key rotation and revocation
  - Usage tracking per key

Keys stored in ~/.aios/api_keys.json
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aictl.core.state import DEFAULT_STATE_DIR


@dataclass
class APIKey:
    key_id: str              # Short ID for display
    key_hash: str            # SHA-256 of the full key (never store raw)
    name: str                # Human-readable label
    created_at: float = 0.0
    expires_at: float = 0.0  # 0 = never
    active: bool = True
    rate_limit_rpm: int = 60     # Requests per minute
    rate_limit_tpm: int = 100000 # Tokens per minute
    total_requests: int = 0
    total_tokens: int = 0


@dataclass
class RateLimitState:
    requests_this_minute: int = 0
    tokens_this_minute: int = 0
    minute_start: float = 0.0


class KeyManager:
    """Manage API keys for the completions proxy."""

    def __init__(self, state_dir: Path | None = None):
        """Initialize API key manager."""
        self.dir = state_dir or DEFAULT_STATE_DIR
        self._keys_path = self.dir / "api_keys.json"
        self._rate_states: dict[str, RateLimitState] = {}

    def generate_key(self, name: str, rate_limit_rpm: int = 60,
                     rate_limit_tpm: int = 100000,
                     expires_days: int = 0) -> tuple[str, APIKey]:
        """Generate a new API key. Returns (raw_key, key_record)."""
        raw_key = f"aios-{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_id = key_hash[:8]

        key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            created_at=time.time(),
            expires_at=time.time() + (expires_days * 86400) if expires_days > 0 else 0,
            active=True,
            rate_limit_rpm=rate_limit_rpm,
            rate_limit_tpm=rate_limit_tpm,
        )

        keys = self._load_keys()
        keys[key_id] = asdict(key)
        self._save_keys(keys)

        return raw_key, key

    def validate(self, raw_key: str) -> tuple[bool, str, APIKey | None]:
        """Validate an API key. Returns (valid, reason, key_record)."""
        if not raw_key or not raw_key.startswith("aios-"):
            return False, "Invalid key format", None

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        keys = self._load_keys()

        # Find by hash — use constant-time comparison to prevent timing attacks
        for kid, kdata in keys.items():
            stored_hash = kdata.get("key_hash", "")
            if stored_hash and secrets.compare_digest(stored_hash, key_hash):
                key = APIKey(**{k: v for k, v in kdata.items() if k in APIKey.__dataclass_fields__})

                if not key.active:
                    return False, "Key revoked", key
                if key.expires_at > 0 and time.time() > key.expires_at:
                    return False, "Key expired", key

                return True, "Valid", key

        return False, "Key not found", None

    def check_rate_limit(self, key: APIKey, tokens: int = 0) -> tuple[bool, str]:
        """Check if request is within rate limits."""
        state = self._rate_states.get(key.key_id, RateLimitState())
        now = time.time()

        # Reset if new minute
        if now - state.minute_start >= 60:
            state = RateLimitState(minute_start=now)

        # Check RPM
        if state.requests_this_minute >= key.rate_limit_rpm:
            return False, f"Rate limit exceeded: {key.rate_limit_rpm} req/min"

        # Check TPM
        if state.tokens_this_minute + tokens > key.rate_limit_tpm:
            return False, f"Token limit exceeded: {key.rate_limit_tpm} tok/min"

        state.requests_this_minute += 1
        state.tokens_this_minute += tokens
        self._rate_states[key.key_id] = state

        return True, "OK"

    def record_usage(self, key_id: str, tokens: int = 0) -> None:
        """Record usage for a key."""
        keys = self._load_keys()
        if key_id in keys:
            keys[key_id]["total_requests"] = keys[key_id].get("total_requests", 0) + 1
            keys[key_id]["total_tokens"] = keys[key_id].get("total_tokens", 0) + tokens
            self._save_keys(keys)

    def revoke(self, key_id: str) -> bool:
        """Revoke."""
        keys = self._load_keys()
        if key_id in keys:
            keys[key_id]["active"] = False
            self._save_keys(keys)
            return True
        return False

    def list_keys(self) -> list[dict[str, Any]]:
        """List keys."""
        keys = self._load_keys()
        result = []
        for kid, kdata in keys.items():
            result.append({
                "key_id": kid,
                "name": kdata.get("name", ""),
                "active": kdata.get("active", True),
                "created_at": kdata.get("created_at", 0),
                "rate_limit_rpm": kdata.get("rate_limit_rpm", 60),
                "total_requests": kdata.get("total_requests", 0),
                "total_tokens": kdata.get("total_tokens", 0),
            })
        return result

    def _load_keys(self) -> dict[str, dict[str, Any]]:
        """Load data from persistent storage."""
        if not self._keys_path.exists():
            return {}
        try:
            data: dict[str, dict[str, Any]] = json.loads(self._keys_path.read_text())
            return data
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_keys(self, keys: dict[str, dict[str, Any]]) -> None:
        """Persist data to storage."""
        self._keys_path.parent.mkdir(parents=True, exist_ok=True)
        self._keys_path.write_text(json.dumps(keys, indent=2))
