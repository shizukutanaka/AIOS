"""Pass 128: engine-type filter flags reject typos (spec improvement #3).

Research-informed (Qiita/Zenn): argparse `choices` accepts *any* object
supporting the `in` operator, so a filter flag can keep its `default=""`
("no filter" / all) while still rejecting a misspelled value. Previously
`engines health --engine vlim`, `optimize --engine ollam`, and
`scale status --engine sglngg` silently meant "no filter" instead of erroring.

`OptionalChoice` (aictl/core/argtypes.py) accepts the empty sentinel via
`__contains__` but iterates only the real engine names, so the usage error
reads "invalid choice: 'vlim' (choose from 'ollama', 'vllm', 'sglang')".
"""

from __future__ import annotations

import argparse
import unittest

from aictl.core.argtypes import OptionalChoice, engine_filter_choices, ENGINE_TYPES


class TestOptionalChoice(unittest.TestCase):
    def test_empty_is_member(self):
        self.assertIn("", engine_filter_choices())

    def test_known_engines_are_members(self):
        c = engine_filter_choices()
        for e in ENGINE_TYPES:
            self.assertIn(e, c)

    def test_typo_is_not_member(self):
        self.assertNotIn("vlim", engine_filter_choices())
        self.assertNotIn("ollam", engine_filter_choices())

    def test_iteration_excludes_empty_sentinel(self):
        # The usage error / metavar must list only real options, never "".
        self.assertEqual(tuple(engine_filter_choices()), ENGINE_TYPES)

    def test_allow_empty_false_rejects_empty(self):
        self.assertNotIn("", OptionalChoice(ENGINE_TYPES, allow_empty=False))


class TestWiredIntoFilterParsers(unittest.TestCase):
    def _parser(self):
        p = argparse.ArgumentParser(prog="t")
        p.add_argument("--engine", default="", choices=engine_filter_choices())
        return p

    def test_empty_default_parses(self):
        self.assertEqual(self._parser().parse_args([]).engine, "")

    def test_valid_engine_parses(self):
        self.assertEqual(self._parser().parse_args(["--engine", "vllm"]).engine, "vllm")

    def test_typo_exits_2(self):
        with self.assertRaises(SystemExit) as cm:
            self._parser().parse_args(["--engine", "vlim"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
