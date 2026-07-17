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
    # SUPPRESS default so the local flag never clobbers a global `aictl --json`.
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="Emit a JSON result summary")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the selftest command."""
    use_json = getattr(args, "json", False)
    tests_dir = Path(__file__).resolve().parent.parent.parent / "tests"
    if not tests_dir.exists():
        if use_json:
            from aictl.core.output import print_json
            print_json({"success": False, "error": f"tests directory not found: {tests_dir}"})
        else:
            print(f"Tests directory not found: {tests_dir}")
        return 1

    loader = unittest.TestLoader()
    suite = loader.discover(str(tests_dir))

    if use_json:
        import io
        runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
        result = runner.run(suite)
        from aictl.core.output import print_json
        print_json({
            "tests_run": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "success": result.wasSuccessful(),
        })
        return 0 if result.wasSuccessful() else 1

    verbosity = 2 if getattr(args, "verbose", False) else 1
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1
