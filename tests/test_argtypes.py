"""Tests for aictl.core.argtypes — parse-time V1 validation (Pass 127).

Research-informed (Qiita/Zenn): the idiomatic argparse pattern is a `type=`
conversion callable that raises ArgumentTypeError, rejecting bad input at parse
time (exit 2) before the handler runs. These types make the V1 invariant
(docs/INPUT_VALIDATION_SPEC.md) reusable instead of per-handler boilerplate.
"""

from __future__ import annotations

import argparse
import unittest

from aictl.core.argtypes import positive_int, nonneg_int


class TestPositiveInt(unittest.TestCase):
    def test_accepts_positive(self):
        self.assertEqual(positive_int("5"), 5)
        self.assertEqual(positive_int("1"), 1)

    def test_rejects_zero(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("0")

    def test_rejects_negative(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("-3")

    def test_rejects_non_integer(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("abc")


class TestNonnegInt(unittest.TestCase):
    def test_accepts_zero_and_positive(self):
        self.assertEqual(nonneg_int("0"), 0)        # 0 = sentinel (e.g. unlimited)
        self.assertEqual(nonneg_int("100"), 100)

    def test_rejects_negative(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            nonneg_int("-1")

    def test_rejects_non_integer(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            nonneg_int("1.5")


class TestWiredIntoParser(unittest.TestCase):
    """End-to-end: a parser using the type rejects bad input at parse time."""

    def _parser(self):
        p = argparse.ArgumentParser(prog="t")
        p.add_argument("--top", type=positive_int, default=5)
        p.add_argument("--quota", type=nonneg_int, default=0)
        return p

    def test_valid_parses(self):
        ns = self._parser().parse_args(["--top", "3", "--quota", "0"])
        self.assertEqual((ns.top, ns.quota), (3, 0))

    def test_parse_time_rejection_exits_2(self):
        # argparse turns ArgumentTypeError into SystemExit(2) — the standard
        # usage-error convention aictl already uses for invalid choices.
        with self.assertRaises(SystemExit) as cm:
            self._parser().parse_args(["--top", "-1"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
