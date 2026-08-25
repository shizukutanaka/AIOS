"""Keep the docs' counts honest — the last step, not the first.

Musk's algorithm puts automate last for a reason: automating a process you do
not yet understand just makes the wrong thing happen faster. This one earned
its place. Over the course of one working session the same three numbers in
CLAUDE.md were hand-edited about a dozen times, always with the same `sed`,
always after the same trigger (the suite grew). That is a process understood
well enough to hand over.

What it replaces:

    sed -i '5s/3734+ tests/3751+ tests/; 22s/273 test files/274 test files/; ...'

typed from memory, where a miscount silently ships a lie about the project and
nothing catches it. The numbers are load-bearing — they are the first thing a
reader (human or agent) sees about the codebase's size.

Deliberately split into check and fix. `check_counts` is pure and cheap, so
`aictl gate` can call it on every run; `sync_counts` writes. A checker that
silently rewrote files during a verification run would be a worse tool than
the sed it replaced.

The test *count* is not recomputed here — running the suite to check a comment
would cost 57s. The gate already knows the number, having just run them, so it
passes it in. Counting the things that are cheap to count (files, commands)
and accepting the one expensive number from whoever already paid for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# Documents whose counts are kept honest. RELEASE.md matters most: it is the
# text that becomes the public release announcement, so a stale number there
# is a false claim shipped to everyone, not just a stale comment.
_TRACKED_DOCS = ("CLAUDE.md", "RELEASE.md")


@dataclass
class CountMismatch:
    """One stale number in the docs."""
    label: str
    documented: int
    actual: int
    line: int = 0

    def __str__(self) -> str:
        # The label already names the document; repeating a hardcoded
        # "CLAUDE.md" here mislabelled every RELEASE.md finding.
        return f"{self.label}: documented {self.documented}, actual {self.actual}"


def count_test_files(root: Path | None = None) -> int:
    """Number of test modules. Cheap — a directory listing."""
    tests = (root or Path(".")) / "tests"
    return len(list(tests.glob("test_*.py"))) if tests.is_dir() else 0


def count_commands(root: Path | None = None) -> int:
    """Number of registered CLI commands, by counting cmd modules.

    Counts files rather than importing and introspecting the parser: this runs
    inside `gate`, and a doc check that could fail on an import error would be
    reporting the wrong problem.
    """
    cmd_dir = (root or Path(".")) / "aictl" / "cmd"
    if not cmd_dir.is_dir():
        return 0
    return len([p for p in cmd_dir.glob("*.py")
                if not p.name.startswith("__")])


def check_counts(root: Path | None = None, test_count: int = 0) -> list[CountMismatch]:
    """Compare the tracked docs' numbers against reality. Pure; never writes.

    `test_count` of 0 means "not supplied" — the test-count claims are then
    skipped rather than compared against a number nobody measured.
    """
    base = root or Path(".")
    problems: list[CountMismatch] = []
    actual_files = count_test_files(base)
    surface = _surface_counts(base)

    for name in _TRACKED_DOCS:
        doc = base / name
        if not doc.is_file():
            continue
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"(\d+)\s+test files", line)
            if m and int(m.group(1)) != actual_files:
                problems.append(CountMismatch(f"{name} test files",
                                              int(m.group(1)), actual_files, i))
            if test_count:
                # Match both "3783+ tests" and "3,783+ tests" — RELEASE.md
                # writes the thousands separator, CLAUDE.md does not, and a
                # checker that only understood one format would silently pass
                # a stale number in the other.
                for m2 in re.finditer(r"([\d,]+)\+?\s+tests", line):
                    documented = int(m2.group(1).replace(",", ""))
                    if documented != test_count:
                        problems.append(CountMismatch(f"{name} tests",
                                                      documented, test_count, i))
            # The surface numbers. These were documented and never verified:
            # "80 Python + 29 Go commands" and "19 MCP tools" are the first
            # claims any reader meets, and nothing compared them to reality.
            for label, pattern, actual in _surface_claims(surface):
                if actual is None:
                    continue
                for m3 in re.finditer(pattern, line):
                    documented = int(m3.group(1))
                    if documented != actual:
                        problems.append(CountMismatch(f"{name} {label}",
                                                      documented, actual, i))
    return problems


def _surface_counts(base: Path) -> dict[str, int | None]:
    """The real sizes of the surfaces the docs advertise.

    Each is best-effort and independently degradable: a count that cannot be
    determined becomes None and is skipped rather than compared against zero,
    which would report every document as wrong. Comparing a documented number
    against a number nobody measured is worse than not checking at all — the
    same rule `test_count=0` already follows.
    """
    counts: dict[str, int | None] = {"python": None, "go": None, "mcp": None}
    try:
        from aictl.core.cli_surface import registered_commands
        counts["python"] = len(registered_commands())
    except Exception:
        pass
    try:
        from aictl.core.goport import go_command_count
        counts["go"] = go_command_count(base) or None
    except Exception:
        pass
    try:
        from aictl.core.cli_surface import mcp_declared_tools
        counts["mcp"] = len(mcp_declared_tools()) or None
    except Exception:
        pass
    return counts


def _surface_claims(surface: dict[str, int | None]):
    """(label, regex, actual) for each surface claim the docs make."""
    return (
        ("Python commands", r"(\d+)\s+Python\s*\+", surface["python"]),
        ("Python commands", r"(\d+)\s+CLI commands", surface["python"]),
        ("Go commands", r"\+\s*(\d+)\s+Go\b", surface["go"]),
        ("MCP tools", r"(\d+)\s+MCP tools", surface["mcp"]),
    )


def sync_counts(root: Path | None = None, test_count: int = 0) -> list[CountMismatch]:
    """Rewrite stale numbers in the tracked docs. Returns what was changed."""
    base = root or Path(".")
    fixed = check_counts(base, test_count)
    if not fixed:
        return []

    actual_files = count_test_files(base)
    surface = _surface_counts(base)
    for name in _TRACKED_DOCS:
        doc = base / name
        if not doc.is_file():
            continue
        # Each doc keeps its own thousands-separator convention, so rewrite in
        # the style already present rather than imposing one.
        grouped = f"{test_count:,}"
        out = []
        for line in doc.read_text(encoding="utf-8").splitlines(keepends=True):
            line = re.sub(r"\d+(\s+test files)", rf"{actual_files}\1", line)
            if test_count:
                line = re.sub(r"\d[\d,]*(\+?\s+tests)",
                              lambda m: (grouped if "," in m.group(0)
                                         else str(test_count)) + m.group(1), line)
            for _label, pattern, actual in _surface_claims(surface):
                if actual is not None:
                    line = re.sub(pattern,
                                  lambda m, a=actual: m.group(0).replace(
                                      m.group(1), str(a), 1), line)
            out.append(line)
        doc.write_text("".join(out), encoding="utf-8")
    return fixed
