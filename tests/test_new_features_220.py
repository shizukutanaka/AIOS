"""Pass 220: three models the filter that exists to find them could not find.

The model catalog tags each entry with a use case, and `aictl recommend
--use-case` filters on it. The choice list was written by hand and had drifted
from the data: the catalog holds six use cases, the flag offered five.

    catalog: chat(24) code(4) embedding(3) vision(2) stt(1) reasoning(3)
    flag:    chat     code    embedding    vision    stt

So `aictl recommend --use-case reasoning` was an argparse error, and
`qwen3:7b-thinking`, `qwen3:32b-thinking` and `phi4-reasoning:14b` — the three
best local reasoning models in the catalog — were unreachable through the
filter that exists to reach them. The MCP tool schema had the same gap, so a
client was told `reasoning` was not a valid value.

Both are now derived from the catalog, like the CLI surface, the MCP dispatch
table and the runtime images before them.

**What this pass deliberately did not do.** The catalog also asserts external
facts — 31 Ollama model names — and several look questionable next to a
registry that hosts text LLMs (`whisper:large-v3` under runtime `ollama`, for
one). `ollama.com` and `registry.ollama.ai` are both outside this
environment's egress allowlist, checked by two independent paths, so those
names could not be verified. They are therefore left exactly as they are:
guessing corrections to unverifiable external facts is what produced the
fabricated `go.sum` and the two container images that named nothing. An
unverified name that happens to be right is better than a confident one that
is wrong.
"""

from __future__ import annotations

import argparse
import unittest

from aictl.runtime.recommend import MODELS, catalog_use_cases


def _cli_use_case_choices() -> set[str]:
    from aictl.__main__ import build_parser

    sub = next(a for a in build_parser()._actions
               if isinstance(a, argparse._SubParsersAction))
    action = next(a for a in sub.choices["recommend"]._actions
                  if a.dest == "use_case")
    return {c for c in action.choices if c}


def _mcp_use_case_enum() -> set[str]:
    from aictl.mcp_server import TOOLS

    tool = next(t for t in TOOLS if t["name"] == "aictl_recommend")
    enum = tool["inputSchema"]["properties"]["use_case"]["enum"]
    return {v for v in enum if v}


class TestCatalogUseCases(unittest.TestCase):
    def test_derived_from_the_catalog(self):
        self.assertEqual(set(catalog_use_cases()),
                         {m.use_case for m in MODELS if m.use_case})

    def test_reasoning_is_present(self):
        # The case that existed in the data and in neither interface.
        self.assertIn("reasoning", catalog_use_cases())

    def test_sorted_and_deduplicated(self):
        uses = catalog_use_cases()
        self.assertEqual(uses, sorted(set(uses)))

    def test_no_empty_entry(self):
        self.assertNotIn("", catalog_use_cases())


class TestBothInterfacesMatchTheCatalog(unittest.TestCase):
    """The drift, in both directions, for both surfaces."""

    def test_cli_offers_every_catalog_use_case(self):
        self.assertEqual(set(catalog_use_cases()) - _cli_use_case_choices(),
                         set(), "a catalogued use case cannot be filtered for")

    def test_cli_offers_nothing_the_catalog_lacks(self):
        # The other direction: a choice with no models behind it returns an
        # empty list and looks like a hardware problem to the user.
        self.assertEqual(_cli_use_case_choices() - set(catalog_use_cases()),
                         set())

    def test_mcp_enum_matches_the_catalog(self):
        self.assertEqual(_mcp_use_case_enum(), set(catalog_use_cases()))

    def test_cli_and_mcp_agree(self):
        self.assertEqual(_cli_use_case_choices(), _mcp_use_case_enum())

    def test_mcp_description_lists_the_same_cases(self):
        # The prose beside the enum is what a model actually reads.
        from aictl.mcp_server import TOOLS

        tool = next(t for t in TOOLS if t["name"] == "aictl_recommend")
        description = tool["inputSchema"]["properties"]["use_case"]["description"]
        for use in catalog_use_cases():
            self.assertIn(use, description)


class TestReasoningModelsAreReachable(unittest.TestCase):
    def test_the_flag_accepts_reasoning(self):
        import io
        from contextlib import redirect_stdout

        from aictl.__main__ import build_parser

        parser = build_parser()
        args = parser.parse_args(["recommend", "--use-case", "reasoning"])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            args.func(args)
        self.assertIn("reasoning", buffer.getvalue())

    def test_the_catalog_still_holds_them(self):
        names = {m.name for m in MODELS if m.use_case == "reasoning"}
        self.assertIn("phi4-reasoning:14b", names)
        self.assertGreaterEqual(len(names), 3)


class TestNoHardcodedUseCaseList(unittest.TestCase):
    """Guards the regression: this bug was a hand-written list."""

    def test_cli_does_not_hardcode_the_choices(self):
        from pathlib import Path

        source = Path("aictl/cmd/recommend.py").read_text()
        self.assertIn("catalog_use_cases", source)
        self.assertNotIn('choices=["chat", "code", "embedding", "vision", "stt", ""]',
                         source)

    def test_mcp_does_not_hardcode_the_enum(self):
        from pathlib import Path

        source = Path("aictl/mcp_server.py").read_text()
        self.assertIn("_catalog_use_cases", source)
        self.assertNotIn('"enum": ["", "chat", "code", "embedding", "vision", "stt"]',
                         source)

    def test_mcp_helper_never_raises(self):
        from unittest.mock import patch

        import aictl.mcp_server as mcp

        # A broken catalog import must not stop the server advertising tools.
        with patch("aictl.runtime.recommend.catalog_use_cases",
                   side_effect=RuntimeError("boom")):
            self.assertTrue(mcp._catalog_use_cases())


if __name__ == "__main__":
    unittest.main()
