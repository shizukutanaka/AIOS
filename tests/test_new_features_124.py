"""Pass 124 (loop): `rag -k` negative-slice trap (Pass 112/121 class).

Hybrid retrieval ended with `reciprocal_rank_fusion([...])[:k]`. With a negative
k this is the slice trap — `[:-3]` returns *all but the last 3* fused results,
the inverse of "top-k" — so `aictl rag search "q" -k -3` returned more (and
wrong) results than asked, and `-k 0` was an empty/degenerate query.

Fixes, defense-in-depth (mirrors warmup Pass 121):
  - library `search()` returns [] for k <= 0, so no caller can trigger the
    inverted slice (and `answer()` retrieves via `search`, so it's covered too);
  - CLI `rag ask` / `rag search` reject `-k < 1` with a clear message and a
    non-zero exit.
"""

from __future__ import annotations

import argparse
import unittest


class TestRagSearchKGuard(unittest.TestCase):
    def test_negative_k_returns_empty_not_inverted_slice(self):
        from aictl.core.rag import search, RagStore
        # k<=0 short-circuits to [] before any store access.
        self.assertEqual(search("hello world", RagStore(), k=-3), [])

    def test_zero_k_returns_empty(self):
        from aictl.core.rag import search, RagStore
        self.assertEqual(search("hello world", RagStore(), k=0), [])


class TestRagCmdKValidation(unittest.TestCase):
    def test_search_negative_k_rejected(self):
        from aictl.cmd.rag import run_search
        self.assertEqual(run_search(argparse.Namespace(query="q", k=-3,
                                                       json=False)), 1)

    def test_search_zero_k_rejected(self):
        from aictl.cmd.rag import run_search
        self.assertEqual(run_search(argparse.Namespace(query="q", k=0,
                                                       json=False)), 1)

    def test_ask_negative_k_rejected(self):
        from aictl.cmd.rag import run_ask
        self.assertEqual(run_ask(argparse.Namespace(question="q?", k=-1,
                                                    json=False)), 1)


if __name__ == "__main__":
    unittest.main()
