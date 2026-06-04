"""aictl selftest — run internal test suite."""

from __future__ import annotations

from typing import Any

import argparse

import unittest
from pathlib import Path


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("selftest", help="Run internal test suite")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the selftest command."""
    tests_dir = Path(__file__).resolve().parent.parent.parent / "tests"
    if not tests_dir.exists():
        print(f"Tests directory not found: {tests_dir}")
        return 1

    verbosity = 2 if getattr(args, "verbose", False) else 1
    loader = unittest.TestLoader()
    suite = loader.discover(str(tests_dir))
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1
