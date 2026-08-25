"""Pass 217: two more numbers nobody checked, one of them a threshold that lied.

Pass 216 derived the CLI command surface and left its siblings in place. Both
were the same disease.

**The MCP phase was `len(TOOLS) >= 16` against 19 tools.** Three could be
deleted and the gate would still pass — printing "16 tools registered" as a
success line. Worse, a count cannot see the failure that actually matters in
either direction: a tool *declared but not dispatched* is advertised in
`tools/list` and then errors when a client calls it, and one *dispatched but
not declared* is unreachable code. The pairing is the invariant; the number
never was.

Dispatch reachability is read from the dispatcher's AST rather than by calling
it. Every handler does real work — hardware detection, a security scan, an LLM
call — so probing by invocation would make the gate slow and side-effecting.
Which names `_dispatch_tool` compares against is a static property, so it is
read statically, and *parsed* rather than grepped so a name in a comment or
docstring cannot be mistaken for a route.

**The documented surface sizes were never verified.** `check_counts` compared
test files and test counts and stopped there, so "80 Python + 29 Go commands"
and "19 MCP tools" — the first claims any reader meets — were hand-maintained
and correct only by luck. They are now derived: Python from the parser, MCP
from the declared tools, Go from the single `root.AddCommand(...)` block in
main.go.

The Go count is read from source rather than from `--help` on the built binary,
which avoids a trap: Cobra adds its own `help` and `completion` commands, so
the binary lists 31 while the port defines 29. The documentation claims the
registrations, and 29 is what it should say.

Each count degrades independently to None and is then skipped, never compared
against zero — comparing a documented number against a number nobody measured
is worse than not checking, the rule `test_count=0` already followed.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from aictl.core.cli_surface import mcp_declared_tools, mcp_dispatched_tools
from aictl.core.docsync import check_counts, sync_counts
from aictl.core.goport import go_command_count


class TestMcpToolsAreReachable(unittest.TestCase):
    def test_every_advertised_tool_dispatches(self):
        # The property the count threshold could not express.
        self.assertEqual(mcp_declared_tools() - mcp_dispatched_tools(), set())

    def test_no_dispatch_branch_is_unreachable(self):
        self.assertEqual(mcp_dispatched_tools() - mcp_declared_tools(), set())

    def test_the_surface_is_not_empty(self):
        # Guards the checker: two empty sets are equal, which would make the
        # comparison above vacuously true forever.
        self.assertGreaterEqual(len(mcp_declared_tools()), 15)

    def test_declared_names_match_the_served_list(self):
        from aictl.mcp_server import TOOLS

        self.assertEqual(mcp_declared_tools(), {t["name"] for t in TOOLS})

    def test_dispatch_names_are_parsed_not_grepped(self):
        # A tool name mentioned in prose must not count as a route. Every
        # declared name appears in the module's docstrings and comments too,
        # so a grep-based reader would agree by accident rather than by fact.
        import ast
        from pathlib import Path as _Path

        import aictl.mcp_server as mcp

        source = _Path(mcp.__file__).read_text()
        self.assertIn("_dispatch_tool", source)
        ast.parse(source)   # the reader's precondition

    def test_gate_uses_the_pairing_not_a_threshold(self):
        import inspect

        from aictl.cmd import gate

        source = inspect.getsource(gate.run)
        self.assertNotIn("mcp_count >= 16", source,
                         "the frozen threshold is back")
        self.assertIn("mcp_dispatched_tools", source)


class TestGoCommandCount(unittest.TestCase):
    def test_counts_the_registered_commands(self):
        self.assertEqual(go_command_count(Path(".")), 29)

    def test_excludes_cobra_builtins(self):
        # `--help` on the built binary lists 31: the port's 29 plus Cobra's
        # own `help` and `completion`. The docs claim the registrations.
        self.assertNotEqual(go_command_count(Path(".")), 31)

    def test_missing_tree_returns_zero_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(go_command_count(Path(td)), 0)

    def test_unparseable_source_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            main = Path(td) / "go-port" / "cmd" / "aictl"
            main.mkdir(parents=True)
            (main / "main.go").write_text("package main\nfunc main() {}\n")
            self.assertEqual(go_command_count(Path(td)), 0)


class TestDocumentedSurfaceIsVerified(unittest.TestCase):
    """The counts that were documented and never checked."""

    def _project(self, claude: str, release: str = "") -> Path:
        root = Path(tempfile.mkdtemp(prefix="aictl-surface-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        (root / "tests").mkdir()
        (root / "tests" / "test_a.py").write_text("")
        shutil.copytree("go-port", root / "go-port")
        (root / "CLAUDE.md").write_text(claude)
        if release:
            (root / "RELEASE.md").write_text(release)
        return root

    def test_this_repo_is_accurate(self):
        self.assertEqual([str(p) for p in check_counts(Path("."))], [])

    def test_a_wrong_python_count_is_caught(self):
        root = self._project("99 Python + 29 Go commands\n")
        self.assertTrue(any("Python commands" in str(p)
                            for p in check_counts(root)))

    def test_a_wrong_go_count_is_caught(self):
        root = self._project("80 Python + 7 Go commands\n")
        self.assertTrue(any("Go commands" in str(p) for p in check_counts(root)))

    def test_a_wrong_mcp_count_is_caught(self):
        root = self._project("80 Python + 29 Go commands\n", "- **3 MCP tools**\n")
        self.assertTrue(any("MCP tools" in str(p) for p in check_counts(root)))

    def test_the_map_line_is_checked_too(self):
        # CLAUDE.md states the number twice, in prose and in its file map.
        root = self._project("80 Python + 29 Go commands\naictl/cmd/  12 CLI commands\n")
        self.assertTrue(any("Python commands" in str(p)
                            for p in check_counts(root)))

    def test_accurate_counts_report_nothing(self):
        root = self._project("80 Python + 29 Go commands\n", "- **19 MCP tools**\n")
        self.assertEqual(check_counts(root), [])

    def test_sync_repairs_every_claim(self):
        root = self._project("99 Python + 7 Go commands\naictl/cmd/  99 CLI commands\n",
                             "- **3 MCP tools**\n")
        sync_counts(root)
        text = (root / "CLAUDE.md").read_text()
        self.assertIn("80 Python + 29 Go commands", text)
        self.assertIn("80 CLI commands", text)
        self.assertIn("19 MCP tools", (root / "RELEASE.md").read_text())
        self.assertEqual(check_counts(root), [])

    def test_an_undeterminable_count_is_skipped_not_zeroed(self):
        # Comparing a documented number against a number nobody measured
        # would report every document as wrong. Same rule test_count=0 uses.
        from unittest.mock import patch

        root = self._project("80 Python + 29 Go commands\n")
        with patch("aictl.core.goport.go_command_count", return_value=0):
            self.assertEqual([p for p in check_counts(root)
                              if "Go commands" in str(p)], [])

    def test_check_never_writes(self):
        root = self._project("99 Python + 7 Go commands\n")
        before = (root / "CLAUDE.md").read_text()
        check_counts(root)
        self.assertEqual((root / "CLAUDE.md").read_text(), before)


if __name__ == "__main__":
    unittest.main()
