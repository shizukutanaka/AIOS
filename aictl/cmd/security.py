"""aictl security — security scanning and hardening checks."""

from __future__ import annotations

from typing import Any

import argparse

from pathlib import Path
from aictl.core.output import ok, err, print_json
from aictl.core.security import scan


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("security", help="Security scanning")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the security command."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    report = scan(state_dir)

    if getattr(args, "json", False):
        from dataclasses import asdict
        print_json(asdict(report))
        return 0

    # Score display
    if report.score >= 80:
        ok(f"Security Score: {report.score}/100")
    elif report.score >= 50:
        print(f"\u26a0 Security Score: {report.score}/100")
    else:
        err(f"Security Score: {report.score}/100")

    print(f"  Passed: {report.checks_passed}/{report.checks_total}")
    print()

    if report.findings:
        severity_icons = {"critical": "\u2717", "high": "\u2717", "medium": "\u26a0", "low": "\u00b7", "info": "\u00b7"}
        for f in sorted(report.findings, key=lambda x: ["critical", "high", "medium", "low", "info"].index(x.severity)):
            icon = severity_icons.get(f.severity, "?")
            print(f"  {icon} [{f.severity.upper():8s}] {f.title}")
            print(f"    {f.description}")
            if f.remediation:
                print(f"    Fix: {f.remediation}")
            print()
    else:
        ok("No security issues found")

    return 0 if report.score >= 50 else 1
