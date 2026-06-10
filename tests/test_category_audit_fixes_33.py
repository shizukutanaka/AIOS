"""Pass 33 regression tests: Python command count stale (65→66) across docs."""

import pathlib
import unittest


class TestPythonCommandCount(unittest.TestCase):
    """Python command count must be consistent at 66 across all docs and code."""

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
            'CLAUDE.md still says "65 Python" in header — update to "66 Python".',
        )

    def test_claude_md_header_has_66(self):
        """CLAUDE.md header must say '66 Python'."""
        self.assertIn(
            "66 Python",
            self._claude_md(),
            'CLAUDE.md header must contain "66 Python + 29 Go commands".',
        )

    def test_claude_md_map_not_65(self):
        """CLAUDE.md map section must not say '65 CLI commands'."""
        self.assertNotIn(
            "65 CLI commands",
            self._claude_md(),
            'CLAUDE.md map still says "65 CLI commands" — update to "66 CLI commands".',
        )

    def test_claude_md_map_has_66(self):
        """CLAUDE.md map section must say '66 CLI commands'."""
        self.assertIn(
            "66 CLI commands",
            self._claude_md(),
            'CLAUDE.md map must contain "66 CLI commands".',
        )

    def test_go_port_json_not_65(self):
        """go-port main.go python_commands must not be 65."""
        self.assertNotIn(
            '"python_commands": 65',
            self._go_main(),
            'go-port/cmd/aictl/main.go still has "python_commands": 65.',
        )

    def test_go_port_json_has_66(self):
        """go-port main.go python_commands must be 66."""
        self.assertIn(
            '"python_commands": 66',
            self._go_main(),
            'go-port/cmd/aictl/main.go must have "python_commands": 66.',
        )

    def test_go_port_text_not_65(self):
        """go-port text output must not say '65 Python'."""
        self.assertNotIn(
            "29 Go + 65 Python",
            self._go_main(),
            'go-port/cmd/aictl/main.go text output still says "29 Go + 65 Python".',
        )

    def test_go_port_text_has_66(self):
        """go-port text output must say '66 Python'."""
        self.assertIn(
            "29 Go + 66 Python",
            self._go_main(),
            'go-port/cmd/aictl/main.go text output must say "29 Go + 66 Python".',
        )

    def test_info_py_fallback(self):
        """aictl/cmd/info.py fallback command count must not be 58."""
        src = (
            pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "info.py"
        ).read_text()
        self.assertNotIn(
            "return 58",
            src,
            "aictl/cmd/info.py fallback is still 58 — update to 66.",
        )

    def test_actual_command_count(self):
        """aictl/__main__.py parser must expose at least 66 subcommands."""
        try:
            from aictl.__main__ import build_parser
            p = build_parser()
            for action in p._actions:
                if hasattr(action, "choices") and action.choices:
                    count = len(action.choices)
                    self.assertGreaterEqual(
                        count, 66,
                        f"Expected at least 66 commands in parser, got {count}",
                    )
                    return
        except Exception as exc:
            self.fail(f"Could not build parser: {exc}")


if __name__ == "__main__":
    unittest.main()
