"""Semantic response cache — local alternative to Portkey's semantic caching.

Portkey offers semantic caching (40% cost reduction) but only as SaaS.
This implements the same idea locally using aictl's existing embedding layer.

How it works:
  1. On each request, embed the prompt
  2. Check if any cached entry has cosine similarity > threshold (default 0.92)
  3. If yes, return the cached response (cache hit, zero inference cost)
  4. If no, run inference, store (embedding, response) in the cache

The cache is backed by SQLite (same pattern as RAG store) so it survives
process restarts and is bounded in size.

Apple principle: zero configuration required. Enable with one line:
    aictl.ai.enable_semantic_cache()
"""

from __future__ import annotations

from typing import Any

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_THRESHOLD = 0.92  # Cosine similarity for a cache hit
DEFAULT_MAX_ENTRIES = 10_000
DEFAULT_TTL_SECONDS = 3600 * 24  # 1 day


@dataclass
class CacheEntry:
    key_hash: str         # Hash of (model, prompt[:64]) for fast lookup
    prompt_hash: str      # SHA256 of full prompt
    prompt_snippet: str   # First 100 chars for debugging
    response: str
    model: str
    embedding: list[float]
    tokens_saved: int
    created_at: float
    hits: int = 0


class SemanticCache:
    """SQLite-backed semantic response cache.

    Thread-safe for single-process use (SQLite WAL mode).
    """

    def __init__(
        self,
        db_path: Path | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        """Initialize the instance with provided arguments."""
        if db_path is None:
            base = os.environ.get("AIOS_STATE_DIR", os.path.expanduser("~/.aios"))
            db_path = Path(base) / "sem_cache.db"
        self.db_path = db_path
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

        # Stats (in-memory per process)
        self._hits = 0
        self._misses = 0
        self._total_tokens_saved = 0

    def _connect(self) -> sqlite3.Connection:
        """Establish a connection."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        """Initialize the SQLite schema for the semantic cache."""
        with self._connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS cache (
                    key_hash TEXT NOT NULL,
                    prompt_hash TEXT PRIMARY KEY,
                    prompt_snippet TEXT,
                    response TEXT NOT NULL,
                    model TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    tokens_saved INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_hit_at REAL DEFAULT 0,
                    hits INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_key_hash ON cache(key_hash);
                CREATE INDEX IF NOT EXISTS idx_created ON cache(created_at);
            """)

    # ── Lookup ────────────────────────────────────────────

    def lookup(self, prompt: str, model: str) -> CacheEntry | None:
        """Find a semantically similar cached response.

        Returns None on cache miss. Caller should run inference and
        call .store() with the result.
        """
        # Fast path: exact hash match
        exact = self._exact_lookup(prompt, model)
        if exact:
            self._hits += 1
            self._total_tokens_saved += exact.tokens_saved
            self._bump_hits(exact.prompt_hash)
            return exact

        # Semantic path: embed and compare
        try:
            from aictl.core.rag import embed_text, cosine
            [query_vec] = embed_text([prompt])
        except Exception:
            self._misses += 1
            return None

        now = time.time()
        best: CacheEntry | None = None
        best_score = 0.0

        with self._connect() as c:
            key_hash = self._key_hash(model)
            rows = c.execute(
                "SELECT prompt_hash, prompt_snippet, response, model, "
                "embedding, tokens_saved, created_at, hits "
                "FROM cache WHERE key_hash = ? AND created_at > ?",
                (key_hash, now - self.ttl_seconds),
            ).fetchall()

        for row in rows:
            ph, snippet, resp, mdl, emb_json, tok, created, hits = row
            try:
                emb = json.loads(emb_json)
            except Exception:
                continue
            score = cosine(query_vec, emb)
            if score >= self.threshold and score > best_score:
                best_score = score
                best = CacheEntry(
                    key_hash=key_hash,
                    prompt_hash=ph,
                    prompt_snippet=snippet,
                    response=resp,
                    model=mdl,
                    embedding=emb,
                    tokens_saved=tok,
                    created_at=created,
                    hits=hits,
                )

        if best:
            self._hits += 1
            self._total_tokens_saved += best.tokens_saved
            self._bump_hits(best.prompt_hash)
            return best

        self._misses += 1
        return None

    def _exact_lookup(self, prompt: str, model: str) -> CacheEntry | None:
        """Exact SHA256 match — fastest path."""
        ph = hashlib.sha256(f"{model}:{prompt}".encode("utf-8")).hexdigest()
        with self._connect() as c:
            row = c.execute(
                "SELECT prompt_hash, prompt_snippet, response, model, "
                "embedding, tokens_saved, created_at, hits "
                "FROM cache WHERE prompt_hash = ? AND created_at > ?",
                (ph, time.time() - self.ttl_seconds),
            ).fetchone()
        if row:
            ph2, snippet, resp, mdl, emb_json, tok, created, hits = row
            try:
                emb = json.loads(emb_json)
            except Exception:
                return None
            return CacheEntry(
                key_hash=self._key_hash(model),
                prompt_hash=ph2,
                prompt_snippet=snippet,
                response=resp,
                model=mdl,
                embedding=emb,
                tokens_saved=tok,
                created_at=created,
                hits=hits,
            )
        return None

    # ── Store ─────────────────────────────────────────────

    def store(
        self,
        prompt: str,
        response: str,
        model: str,
        tokens: int = 0,
        embedding: list[float] | None = None,
    ) -> None:
        """Store a response in the semantic cache."""
        if embedding is None:
            try:
                from aictl.core.rag import embed_text
                [embedding] = embed_text([prompt])
            except Exception:
                return  # Can't embed → can't cache semantically

        prompt_hash = hashlib.sha256(
            f"{model}:{prompt}".encode("utf-8")
        ).hexdigest()
        key_hash = self._key_hash(model)

        with self._connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO cache "
                "(key_hash, prompt_hash, prompt_snippet, response, model, "
                "embedding, tokens_saved, created_at, hits) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    key_hash, prompt_hash, prompt[:100],
                    response, model,
                    json.dumps(embedding),
                    tokens, time.time(),
                ),
            )
        self._evict_if_needed()

    # ── Maintenance ───────────────────────────────────────

    def _bump_hits(self, prompt_hash: str) -> None:
        """Increment the hit counter for a cache entry."""
        try:
            with self._connect() as c:
                c.execute(
                    "UPDATE cache SET hits = hits + 1, last_hit_at = ? "
                    "WHERE prompt_hash = ?",
                    (time.time(), prompt_hash),
                )
        except Exception:
            pass  # best-effort; failure is non-critical

    def _evict_if_needed(self) -> None:
        """Delete oldest entries when the cache exceeds max_entries."""
        try:
            with self._connect() as c:
                count = c.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
                if count > self.max_entries:
                    delete_n = count - self.max_entries
                    c.execute(
                        "DELETE FROM cache WHERE prompt_hash IN "
                        "(SELECT prompt_hash FROM cache "
                        "ORDER BY last_hit_at ASC, created_at ASC "
                        f"LIMIT {delete_n})"
                    )
        except Exception:
            pass  # best-effort; failure is non-critical

    def clear(self) -> None:
        """Clear stored data."""
        with self._connect() as c:
            c.execute("DELETE FROM cache")

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._connect() as c:
            total = c.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            total_hits = c.execute(
                "SELECT COALESCE(SUM(hits), 0) FROM cache"
            ).fetchone()[0]
            c.execute(
                "SELECT COALESCE(SUM(tokens_saved * hits), 0) FROM cache"
            ).fetchone()[0]

        total_requests = self._hits + self._misses
        hit_rate = self._hits / total_requests if total_requests else 0.0
        return {
            "entries": total,
            "session_hits": self._hits,
            "session_misses": self._misses,
            "session_hit_rate": round(hit_rate, 3),
            "total_tokens_saved": self._total_tokens_saved,
            "lifetime_hits": total_hits,
            "db_path": str(self.db_path),
            "threshold": self.threshold,
        }

    @staticmethod
    def _key_hash(model: str) -> str:
        """Return a stable hash key for a (prompt, model) pair."""
        return hashlib.sha256(model.encode("utf-8")).hexdigest()[:8]


# Process-local default instance
_DEFAULT_CACHE: SemanticCache | None = None


def get_default_cache() -> SemanticCache:
    """Return the global default SemanticCache instance."""
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = SemanticCache()
    return _DEFAULT_CACHE
