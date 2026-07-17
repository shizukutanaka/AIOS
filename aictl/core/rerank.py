"""aictl reranker — pluggable cross-encoder reranking for RAG results.

Optional refinement stage over `core.rag.search()`'s RRF-fused hybrid
retrieval (IMPROVEMENTS.md item A-3). Off by default (`Config.rerank_endpoint
== ""`): a true no-op, zero network calls, RRF order unchanged.

Targets a TEI (HuggingFace Text Embeddings Inference) `/rerank` endpoint --
the only self-hosted reranker HTTP contract independently verified against
its own OpenAPI spec at design time. Ollama has no native rerank endpoint;
vLLM claims Cohere-compatibility for its own `/rerank` but its exact field
names could not be confirmed against vLLM's own docs, so this module does
not special-case it -- point `rerank_endpoint` at any server that speaks
TEI's `/rerank` contract (TEI itself, or an adapter in front of vLLM/other
servers).

Fails closed toward "leave the RRF order alone", not toward raising: an
unreachable endpoint, a timeout, a non-2xx response, malformed JSON, or an
out-of-range/missing `index` in the response all return None rather than
raising or fabricating an order, matching the project's established
guard.make_llm_content_check / sdk._embed "degrade to the pre-existing
behavior" convention for optional model-backed features.
"""

from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from aictl.core.constants import RERANK_TIMEOUT

if TYPE_CHECKING:
    from aictl.core.rag import Chunk


def rerank(
    endpoint: str,
    model: str,
    query: str,
    candidates: list[tuple["Chunk", float]],
    timeout: float | None = None,
) -> list[tuple["Chunk", float]] | None:
    """Re-score and reorder `candidates` via a TEI-compatible /rerank call.

    `candidates` is a best-first list of (Chunk, score) pairs, typically the
    RRF-fused candidate pool from `core.rag.search()` before its final `[:k]`
    truncation. Returns a new best-first list (same Chunks, scores replaced
    by the reranker's own relevance scores) on success, or None on any
    failure -- callers must fall back to the original `candidates` order.
    """
    if not endpoint or not candidates:
        return None
    if urlparse(endpoint).scheme not in ("http", "https"):
        return None

    try:
        payload: dict[str, Any] = {
            "query": query,
            "texts": [chunk.text for chunk, _ in candidates],
            "raw_scores": False,
        }
        if model:
            payload["model"] = model
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint.rstrip("/") + "/rerank",
            data=body, headers={"Content-Type": "application/json"},
            method="POST",
        )
        effective_timeout = timeout if timeout is not None else RERANK_TIMEOUT
        with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
            ranks = json.loads(resp.read())

        if not isinstance(ranks, list) or not ranks:
            return None

        reordered: list[tuple["Chunk", float]] = []
        for entry in ranks:
            idx = entry["index"]
            score = float(entry["score"])
            if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
                return None  # malformed/out-of-range index -- abstain entirely
            reordered.append((candidates[idx][0], score))
        return reordered
    except Exception:
        return None
