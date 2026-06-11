"""Pass 33 regression tests: Python command count stays consistent across docs.

Originally pinned at 66; bumped to 69 after plugin/export/import were added.
The count must stay consistent across CLAUDE.md, go-port, and info.py.
"""

import pathlib
import unittest


class TestPythonCommandCount(unittest.TestCase):
    """Python command count must be consistent across all docs and code."""

    EXPECTED = 70

    @classmethod
    def _claude_md(cls) -> str:
        return (pathlib.Path(__file__).parent.parent / "CLAUDE.md").read_text()

    @classmethod
    def _go_main(cls) -> str:
        return (
            pathlib.Path(__file__).parent.parent
            / "go-port" / "cmd" / "aictl" / "main.go"
        ).read_text()

    def test_claude_md_header_not_65(self):
        """CLAUDE.md header line must not say '65 Python'."""
        self.assertNotIn(
            "65 Python",
            self._claude_md(),
            'CLAUDE.md still says "65 Python" in header.',
        )

    def test_claude_md_header_has_expected(self):
        """CLAUDE.md header must say '<EXPECTED> Python'."""
        self.assertIn(
            f"{self.EXPECTED} Python",
            self._claude_md(),
            f'CLAUDE.md header must contain "{self.EXPECTED} Python + 29 Go commands".',
        )

    def test_claude_md_map_not_65(self):
        """CLAUDE.md map section must not say '65 CLI commands'."""
        self.assertNotIn(
            "65 CLI commands",
            self._claude_md(),
            'CLAUDE.md map still says "65 CLI commands".',
        )

    def test_claude_md_map_has_expected(self):
        """CLAUDE.md map section must say '<EXPECTED> CLI commands'."""
        self.assertIn(
            f"{self.EXPECTED} CLI commands",
            self._claude_md(),
            f'CLAUDE.md map must contain "{self.EXPECTED} CLI commands".',
        )

    def test_go_port_json_has_expected(self):
        """go-port main.go python_commands must equal EXPECTED."""
        self.assertIn(
            f'"python_commands": {self.EXPECTED}',
            self._go_main(),
            f'go-port/cmd/aictl/main.go must have "python_commands": {self.EXPECTED}.',
        )

    def test_go_port_text_has_expected(self):
        """go-port text output must say '<EXPECTED> Python'."""
        self.assertIn(
            f"29 Go + {self.EXPECTED} Python",
            self._go_main(),
            f'go-port/cmd/aictl/main.go text output must say "29 Go + {self.EXPECTED} Python".',
        )

    def test_info_py_fallback(self):
        """aictl/cmd/info.py fallback command count must not be 58."""
        src = (
            pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "info.py"
        ).read_text()
        self.assertNotIn(
            "return 58",
            src,
            "aictl/cmd/info.py fallback is still 58.",
        )

    def test_actual_command_count(self):
        """aictl/__main__.py parser must expose at least EXPECTED subcommands."""
        try:
            from aictl.__main__ import build_parser
            p = build_parser()
            for action in p._actions:
                if hasattr(action, "choices") and action.choices:
                    count = len(action.choices)
                    self.assertGreaterEqual(
                        count, self.EXPECTED,
                        f"Expected at least {self.EXPECTED} commands in parser, got {count}",
                    )
                    return
        except Exception as exc:
            self.fail(f"Could not build parser: {exc}")


if __name__ == "__main__":
    unittest.main()
