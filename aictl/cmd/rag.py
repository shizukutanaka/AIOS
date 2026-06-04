"""aictl rag — zero-config retrieval-augmented generation.

Subcommands:
  aictl rag index <path>      Index a directory or file
  aictl rag ask <question>    Query indexed docs
  aictl rag search <query>    See raw matches without LLM answer
  aictl rag status            Show index size/stats
  aictl rag reset             Wipe the index

Apple principle: a single user interaction completes the job.
"""

from __future__ import annotations

from typing import Any

import argparse

from pathlib import Path

from aictl.core.output import ok, warn, err, print_json


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "rag",
        help="Retrieval-augmented generation (zero-config local RAG).",
    )
    sp = p.add_subparsers(dest="rag_cmd")
    sp.required = True

    p_index = sp.add_parser("index", help="Index a directory of documents.")
    p_index.add_argument("path", help="File or directory to index.")
    p_index.set_defaults(func=run_index)

    p_ask = sp.add_parser("ask", help="Ask a question. Uses indexed docs as context.")
    p_ask.add_argument("question", help="Your question.")
    p_ask.add_argument("-k", type=int, default=5,
                       help="Number of chunks to retrieve (default: 5)")
    p_ask.set_defaults(func=run_ask)

    p_search = sp.add_parser("search", help="Show raw retrieval matches.")
    p_search.add_argument("query", help="Search query.")
    p_search.add_argument("-k", type=int, default=5)
    p_search.set_defaults(func=run_search)

    p_status = sp.add_parser("status", help="Show index statistics.")
    p_status.set_defaults(func=run_status)

    p_reset = sp.add_parser("reset", help="Delete the entire index.")
    p_reset.add_argument("--yes", action="store_true",
                         help="Skip confirmation prompt.")
    p_reset.set_defaults(func=run_reset)


def run_index(args: argparse.Namespace) -> int:
    """Index documents from a directory."""
    from aictl.core.rag import RagStore, index_directory

    target = Path(args.path).expanduser().resolve()
    if not target.exists():
        err(f"Path does not exist: {target}")
        print("  Try: aictl rag index ./docs")
        return 1

    store = RagStore()

    print()
    print(f"  Indexing: {target}")
    print()

    def progress(fpath: Path, n_chunks: int) -> None:
        """Execute progress."""
        rel = fpath.name
        print(f"    + {rel} ({n_chunks} chunks)")

    try:
        stats = index_directory(target, store, progress_callback=progress)
    except FileNotFoundError as e:
        err(str(e))
        return 1

    print()
    if stats["indexed"] == 0:
        if stats["files_total"] == 0:
            warn(f"No files found in {target}")
        else:
            warn(f"Nothing new to index ({stats['skipped']} skipped, "
                 f"others already up-to-date)")
    else:
        ok(f"Indexed {stats['indexed']} files, "
           f"{stats['chunks_created']} chunks "
           f"({stats['skipped']} skipped)")

    if getattr(args, "json", False):
        print_json(stats)

    from aictl.core.next_action import suggest
    suggest("rag_index")
    return 0


def run_ask(args: argparse.Namespace) -> int:
    """Answer a question using indexed documents."""
    from aictl.core.rag import RagStore, answer
    from aictl.core.empty_state import show as show_empty

    store = RagStore()
    if store.stats()["embedded"] == 0:
        show_empty("rag_index")
        return 1

    print()
    print(f"  Question: {args.question}")
    print()
    print("  Searching index...")

    response, sources = answer(args.question, store, k=args.k)

    print()
    if not sources:
        warn("No relevant documents found.")
        return 2

    print("  Answer:")
    print()
    # Indent the response text for readability
    for line in response.splitlines() or [""]:
        print(f"  {line}")
    print()

    print(f"  Sources ({len(sources)} chunks):")
    for chunk, score in sources:
        rel = Path(chunk.source).name
        snippet = chunk.text[:100].replace("\n", " ")
        print(f"    [{score:.3f}] {rel}  —  {snippet}...")
    print()

    if getattr(args, "json", False):
        print_json({
            "question": args.question,
            "answer": response,
            "sources": [
                {
                    "source": c.source,
                    "score": s,
                    "snippet": c.text[:200],
                }
                for c, s in sources
            ],
        })
    return 0


def run_search(args: argparse.Namespace) -> int:
    """Search indexed documents."""
    from aictl.core.rag import RagStore, search
    from aictl.core.empty_state import show as show_empty

    store = RagStore()
    if store.stats()["embedded"] == 0:
        show_empty("rag_index")
        return 1

    matches = search(args.query, store, k=args.k)
    if not matches:
        warn("No relevant documents found.")
        return 2

    if getattr(args, "json", False):
        print_json([
            {"source": c.source, "score": s, "text": c.text}
            for c, s in matches
        ])
        return 0

    print()
    print(f"  Top {len(matches)} matches for: {args.query}")
    print()
    for chunk, score in matches:
        rel = Path(chunk.source).name
        print(f"  [{score:.3f}] {rel} (chunk {chunk.chunk_idx})")
        snippet = chunk.text[:200].replace("\n", " ")
        print(f"        {snippet}...")
        print()

    return 0


def run_status(args: argparse.Namespace) -> int:
    """Show current status."""
    from aictl.core.rag import RagStore

    store = RagStore()
    stats = store.stats()

    if getattr(args, "json", False):
        print_json(stats)
        return 0

    print()
    if stats["documents"] == 0:
        print("  Index is empty.")
        print()
        print("  Try: aictl rag index ./docs")
        print()
        return 0

    print("  RAG Index Status")
    print()
    print(f"    Documents:    {stats['documents']}")
    print(f"    Chunks:       {stats['chunks']}")
    print(f"    Embedded:     {stats['embedded']}")
    print(f"    Database:     {stats['db_path']}")
    print(f"    Size:         {stats['db_size_mb']:.2f} MB")
    print()
    return 0


def run_reset(args: argparse.Namespace) -> int:
    """Reset to empty state."""
    from aictl.core.rag import RagStore

    if not getattr(args, "yes", False):
        warn("This will delete all indexed documents.")
        print("  Re-run with --yes to confirm.")
        return 1

    store = RagStore()
    store.clear()
    ok("Index cleared.")
    return 0
