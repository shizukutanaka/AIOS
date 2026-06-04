"""aictl RAG — zero-config document indexing.

GPT4All has 'LocalDocs': drop a folder, get RAG. We provide the same
experience but as part of aictl's broader infrastructure layer.

Design principles:
  - One command to index: `aictl rag index ./docs`
  - One command to query: `aictl rag ask "What's our refund policy?"`
  - Storage: SQLite + JSON, no external vector DB needed
  - Embeddings: aictl picks the right model for the user's hardware
  - Chunking: smart defaults, no tuning required
  - Files: PDF, Markdown, text, source code (heuristic detection)

Apple principle: a single user interaction completes the job.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


# ─── Configuration ─────────────────────────────────────────

# Chunk size in characters (rough proxy for tokens)
DEFAULT_CHUNK_SIZE = 1500
# Overlap between chunks to preserve context across boundaries
DEFAULT_OVERLAP = 200
# Top-K retrieval default
DEFAULT_K = 5

# File extensions we know how to read
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".org",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".go", ".rs", ".rb", ".java", ".kt", ".scala",
    ".c", ".cpp", ".h", ".hpp", ".cs",
    ".html", ".css", ".sh", ".bash", ".zsh",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".json", ".xml", ".csv", ".tsv",
    ".sql", ".graphql", ".proto",
}
PDF_EXTENSIONS = {".pdf"}
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".mp4", ".mp3", ".wav", ".ogg", ".flac",
    ".zip", ".tar", ".gz", ".bz2", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".class", ".pyc", ".o", ".a",
}


@dataclass
class Chunk:
    """One piece of text from one document."""
    doc_id: str        # SHA256 of source path
    chunk_idx: int     # Position within the document
    source: str        # Original file path
    text: str          # The chunk content
    embedding: list[float] | None = None
    indexed_at: float = 0.0


# ─── Storage ───────────────────────────────────────────────

class RagStore:
    """SQLite-backed chunk and embedding store.

    Schema:
      docs (doc_id PK, source, mtime, file_size)
      chunks (doc_id, chunk_idx, text, embedding_blob, PRIMARY KEY (doc_id, chunk_idx))
    """

    def __init__(self, db_path: Path | None = None):
        """Initialize the instance with provided arguments."""
        if db_path is None:
            base = os.environ.get("AIOS_STATE_DIR", os.path.expanduser("~/.aios"))
            db_path = Path(base) / "rag.db"
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        """Establish a connection."""
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        """Initialize the SQLite schema for the semantic cache."""
        with self._connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS docs (
                    doc_id TEXT PRIMARY KEY,
                    source TEXT UNIQUE NOT NULL,
                    mtime REAL NOT NULL,
                    file_size INTEGER NOT NULL,
                    indexed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    doc_id TEXT NOT NULL,
                    chunk_idx INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding TEXT,
                    PRIMARY KEY (doc_id, chunk_idx),
                    FOREIGN KEY (doc_id) REFERENCES docs(doc_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_doc
                    ON chunks(doc_id);
            """)

    def needs_reindex(self, source: str, mtime: float, size: int) -> bool:
        """True if the doc isn't indexed or has changed since last index."""
        with self._connect() as c:
            row = c.execute(
                "SELECT mtime, file_size FROM docs WHERE source = ?",
                (source,),
            ).fetchone()
        if row is None:
            return True
        return bool(row[0] < mtime or row[1] != size)

    def upsert_doc(
        self, source: str, mtime: float, size: int, chunks: list[Chunk],
    ) -> None:
        """Replace any existing record for this source with the new chunks."""
        doc_id = chunks[0].doc_id if chunks else _doc_id_for(source)
        with self._connect() as c:
            # Find old doc_id (in case source matches but doc_id differs)
            row = c.execute(
                "SELECT doc_id FROM docs WHERE source = ?", (source,)
            ).fetchone()
            if row:
                old_doc_id = row[0]
                c.execute("DELETE FROM chunks WHERE doc_id = ?", (old_doc_id,))
            # Also clean up any chunks for the new doc_id (defensive)
            c.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            c.execute("DELETE FROM docs WHERE source = ?", (source,))
            c.execute(
                "INSERT INTO docs (doc_id, source, mtime, file_size, indexed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (doc_id, source, mtime, size, time.time()),
            )
            c.executemany(
                "INSERT INTO chunks (doc_id, chunk_idx, text, embedding) "
                "VALUES (?, ?, ?, ?)",
                [
                    (chunk.doc_id, chunk.chunk_idx, chunk.text,
                     json.dumps(chunk.embedding) if chunk.embedding else None)
                    for chunk in chunks
                ],
            )

    def all_chunks_with_embeddings(self) -> Iterator[Chunk]:
        """Yield every indexed chunk with its embedding loaded."""
        with self._connect() as c:
            for row in c.execute(
                "SELECT c.doc_id, c.chunk_idx, d.source, c.text, c.embedding "
                "FROM chunks c JOIN docs d ON c.doc_id = d.doc_id "
                "WHERE c.embedding IS NOT NULL"
            ):
                doc_id, chunk_idx, source, text, emb_json = row
                yield Chunk(
                    doc_id=doc_id, chunk_idx=chunk_idx, source=source,
                    text=text, embedding=json.loads(emb_json),
                )

    def stats(self) -> dict[str, Any]:
        """Index size / doc count for `aictl rag status`."""
        with self._connect() as c:
            doc_count = c.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
            chunk_count = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            embedded = c.execute(
                "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
            ).fetchone()[0]
        return {
            "documents": doc_count,
            "chunks": chunk_count,
            "embedded": embedded,
            "db_path": str(self.db_path),
            "db_size_mb": self.db_path.stat().st_size / (1024 * 1024)
                          if self.db_path.exists() else 0,
        }

    def clear(self) -> None:
        """Wipe the index. For `aictl rag reset`."""
        with self._connect() as c:
            c.execute("DELETE FROM chunks")
            c.execute("DELETE FROM docs")


# ─── File reading ──────────────────────────────────────────

def _doc_id_for(source: str) -> str:
    """Stable identifier for a file path."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def read_file(path: Path) -> str | None:
    """Read a file's text content. Returns None if unreadable/skip."""
    ext = path.suffix.lower()
    if ext in SKIP_EXTENSIONS:
        return None
    if ext in TEXT_EXTENSIONS or ext == "":
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    if ext in PDF_EXTENSIONS:
        return _read_pdf_minimal(path)
    # Unknown extension — try as text, give up if it's binary-looking
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
        return text
    except (UnicodeDecodeError, OSError):
        return None


def _read_pdf_minimal(path: Path) -> str | None:
    """Best-effort PDF text extraction without external dependencies.

    PDFs without a parser are not really readable in Python's stdlib, so this
    falls back to grep-style extraction of obvious ASCII strings between
    BT/ET markers. Better than nothing; user should install pdfplumber for
    real workloads.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    # Look for text between 'BT' (Begin Text) and 'ET' (End Text) markers.
    # This is rough — does not handle compressed streams.
    matches = re.findall(rb"BT[\s\S]{0,2000}?ET", raw)
    if not matches:
        return None

    # Extract printable ASCII strings inside parentheses (PDF text operators)
    parts = []
    for block in matches:
        for piece in re.findall(rb"\(([^)\\]{2,})\)", block):
            try:
                parts.append(piece.decode("utf-8", errors="replace"))
            except Exception:
                pass  # best-effort; failure is non-critical

    return "\n".join(parts) if parts else None


# ─── Chunking ──────────────────────────────────────────────

def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks.

    Prefers paragraph boundaries; falls back to character boundaries.
    """
    if not text or not text.strip():
        return []
    # Guard against misconfiguration: a non-positive chunk_size, or an overlap
    # that meets/exceeds chunk_size, would make the character-slicing step
    # `chunk_size - overlap` zero (range() raises ValueError) or negative
    # (empty range → the paragraph is silently dropped from the index).
    chunk_size = max(1, chunk_size)
    overlap = max(0, min(overlap, chunk_size - 1))
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    paragraphs = re.split(r"\n\s*\n", text)
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) + 2 <= chunk_size:
            buf = (buf + "\n\n" + para) if buf else para
            continue
        if buf:
            chunks.append(buf)
            # Carry tail of buf forward for overlap
            if len(buf) > overlap:
                buf = buf[-overlap:] + "\n\n" + para
            else:
                buf = para
        else:
            # Single paragraph longer than chunk_size — slice it
            for i in range(0, len(para), chunk_size - overlap):
                piece = para[i:i + chunk_size]
                if piece:
                    chunks.append(piece)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


# ─── Embedding ─────────────────────────────────────────────

def embed_text(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts via the aictl SDK.

    Falls back to a deterministic hash-based "embedding" if no real model
    is reachable. The fallback is not semantically meaningful but keeps the
    pipeline working in tests/dev.
    """
    if not texts:
        return []
    try:
        import aictl
        return aictl.ai.embed(texts)
    except Exception:
        return [_fallback_embedding(t) for t in texts]


def _fallback_embedding(text: str, dim: int = 64) -> list[float]:
    """Deterministic hash embedding for offline tests.

    Uses the byte distribution of the text. Same input → same vector,
    similar inputs → loosely similar vectors. NOT semantic.
    """
    if not text:
        return [0.0] * dim
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    # Repeat digest to fill `dim` values, normalize to [-1, 1]
    raw: list[int] = []
    for _ in range((dim // len(digest)) + 1):
        raw.extend(digest)
    vec = [(b - 128) / 128.0 for b in raw[:dim]]
    return vec


# ─── Similarity ────────────────────────────────────────────

def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0 for empty/mismatched vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


# ─── Pipeline orchestration ────────────────────────────────

def index_directory(
    root: Path,
    store: RagStore,
    progress_callback: Any=None,
) -> dict[str, Any]:
    """Walk a directory, chunk every readable file, embed, store.

    Returns a stats dict suitable for `aictl rag index --json`.
    """
    if not root.exists():
        raise FileNotFoundError(f"No such directory: {root}")
    if not root.is_dir():
        # Treat as a single file
        files = [root]
    else:
        files = [f for f in root.rglob("*") if f.is_file()]

    indexed = 0
    skipped = 0
    chunks_created = 0

    for fpath in files:
        # Heuristic skip
        if fpath.suffix.lower() in SKIP_EXTENSIONS:
            skipped += 1
            continue
        try:
            stat = fpath.stat()
        except OSError:
            skipped += 1
            continue
        if not store.needs_reindex(str(fpath), stat.st_mtime, stat.st_size):
            continue  # already up-to-date

        text = read_file(fpath)
        if text is None or not text.strip():
            skipped += 1
            continue

        chunks_text = chunk_text(text)
        if not chunks_text:
            skipped += 1
            continue

        embeddings = embed_text(chunks_text)
        doc_id = _doc_id_for(str(fpath))
        chunks = [
            Chunk(
                doc_id=doc_id,
                chunk_idx=i,
                source=str(fpath),
                text=t,
                embedding=embeddings[i] if i < len(embeddings) else None,
                indexed_at=time.time(),
            )
            for i, t in enumerate(chunks_text)
        ]
        store.upsert_doc(str(fpath), stat.st_mtime, stat.st_size, chunks)
        indexed += 1
        chunks_created += len(chunks)

        if progress_callback:
            progress_callback(fpath, len(chunks))

    return {
        "indexed": indexed,
        "skipped": skipped,
        "chunks_created": chunks_created,
        "files_total": len(files),
    }


def search(
    query: str,
    store: RagStore,
    k: int = DEFAULT_K,
) -> list[tuple[Chunk, float]]:
    """Return top-K chunks for the query, sorted by similarity desc."""
    if not query.strip():
        return []
    [query_vec] = embed_text([query])

    results: list[tuple[Chunk, float]] = []
    for chunk in store.all_chunks_with_embeddings():
        if chunk.embedding is None:
            continue
        score = cosine(query_vec, chunk.embedding)
        results.append((chunk, score))

    results.sort(key=lambda x: -x[1])
    return results[:k]


def answer(
    question: str,
    store: RagStore,
    k: int = DEFAULT_K,
) -> tuple[str, list[tuple[Chunk, float]]]:
    """Retrieve context, then ask the model. Returns (answer, sources)."""
    matches = search(question, store, k=k)
    if not matches:
        return ("No relevant documents found in the index.", [])

    context_blob = "\n\n---\n\n".join(
        f"[Source: {Path(c.source).name}]\n{c.text}"
        for c, _ in matches
    )

    try:
        import aictl
        response = aictl.ai.ask(
            question,
            context=context_blob,
            mode="factual",
        )
        return (str(response), matches)
    except Exception as e:
        return (f"(Could not reach inference engine: {e})", matches)
