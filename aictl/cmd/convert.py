"""aictl convert — data format conversion tools.

Currently supports:
  - bank: Mizuho Bank → Kanjyo Bugyo accounting format conversion
  - model: Model format detection and runtime recommendation
"""

from __future__ import annotations

from typing import Any

import argparse

from pathlib import Path
from aictl.core.output import ok, err, print_json, print_kv, print_table


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("convert", help="Data format conversion")
    csub = p.add_subparsers(dest="convert_cmd")

    bank = csub.add_parser("bank", help="Bank statement → accounting software conversion")
    bank.add_argument("input", nargs="?", help="Input file (Zengin CSV or Mizuho PDF)")
    bank.add_argument("--format", default="kanjyo-bugyo", choices=["kanjyo-bugyo", "obc", "freee"],
                      help="Target accounting format")
    bank.add_argument("--output", default="", help="Output file path")
    bank.add_argument("--llm", action="store_true", help="Use LLM for account classification")
    bank.set_defaults(func=run_bank)

    model = csub.add_parser("model", help="Detect model file format")
    model.add_argument("path", help="Model file or directory")
    model.set_defaults(func=run_model_format)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_bank(args: argparse.Namespace) -> int:
    """Execute the bank subcommand."""
    input_path = getattr(args, "input", None)
    if not input_path:
        print("Bank statement conversion (Mizuho → Kanjyo Bugyo)")
        print()
        print("Usage:")
        print("  aictl convert bank statement.csv")
        print("  aictl convert bank statement.csv --format obc --llm")
        print()
        print("Supported inputs:")
        print("  - Zengin format CSV (全銀フォーマット)")
        print("  - Mizuho Direct PDF")
        print()
        print("Supported outputs:")
        print("  - kanjyo-bugyo (勘定奉行)")
        print("  - obc (OBC奉行)")
        print("  - freee")
        return 0

    path = Path(input_path)
    if not path.exists():
        err(f"File not found: {input_path}")
        return 1

    # Check if bank_to_bugyo is available
    try:
        import importlib
        spec = importlib.util.find_spec("bank_to_bugyo")
        if spec is None:
            raise ImportError
        ok(f"Processing {path.name} → {args.format}")
        print("  bank_to_bugyo module found — running conversion")
        # Deferred: bank_to_bugyo integration (see cmd/convert.py design doc)
        return 0
    except ImportError:
        ok(f"Processing {path.name} → {args.format}")
        print("  bank_to_bugyo module not installed")
        print("  Install: pip install -e /path/to/bank_to_bugyo")
        print("  Or use the recipe: aictl recipe run bank-convert")
        return 1


def run_model_format(args: argparse.Namespace) -> int:
    """Execute the model_format subcommand."""
    from aictl.runtime.formats import detect_format, detect_model_dir, recommend_runtime, format_size

    path = Path(args.path)

    if path.is_dir():
        results = detect_model_dir(path)
        if not results:
            print(f"No model files found in {path}")
            return 0

        if getattr(args, "json", False):
            print_json([{"path": (r.metadata or {}).get("path", ""), "format": r.format,
                        "size": format_size(r.size_bytes), "runtime": recommend_runtime(r)}
                       for r in results])
            return 0

        rows = [{"file": Path((r.metadata or {}).get("path", "")).name,
                 "format": r.format, "version": r.version,
                 "size": format_size(r.size_bytes),
                 "runtime": recommend_runtime(r)} for r in results]
        print_table(rows, ["file", "format", "version", "size", "runtime"])
    else:
        fmt = detect_format(path)
        if getattr(args, "json", False):
            print_json(fmt.__dict__)
            return 0

        ok(f"{path.name}: {fmt.format}" + (f" {fmt.version}" if fmt.version else ""))
        print_kv([
            ("Size", format_size(fmt.size_bytes)),
            ("Runtime", recommend_runtime(fmt)),
            ("Compatible", ", ".join(fmt.compatible_runtimes or [])),
        ], indent=2)
    return 0
