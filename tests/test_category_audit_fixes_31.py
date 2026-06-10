"""Pass 31 regression tests: MCP tool count stale (16/18→19) across docs."""

import pathlib
import unittest


class TestMcpToolCount(unittest.TestCase):
    """mcp_server.py TOOLS list must stay consistent with documentation."""

    @classmethod
    def _mcp_src(cls) -> str:
        return (
            pathlib.Path(__file__).parent.parent / "aictl" / "mcp_server.py"
        ).read_text()

    @classmethod
    def _readme(cls) -> str:
        return (pathlib.Path(__file__).parent.parent / "README.md").read_text()

    @classmethod
    def _claude_md(cls) -> str:
        return (pathlib.Path(__file__).parent.parent / "CLAUDE.md").read_text()

    def test_actual_tool_count(self):
        """TOOLS list in mcp_server.py must contain exactly 19 tools."""
        src = self._mcp_src()
        # Count tool name entries
        count = src.count('"name": "aictl_')
        self.assertEqual(
            count, 19,
            f"Expected 19 MCP tools in mcp_server.py TOOLS list, got {count}",
        )

    def test_readme_not_16(self):
        """README.md must not advertise stale 16-tool count."""
        self.assertNotIn(
            "16ツール",
            self._readme(),
            'README.md still says "16ツール" — update to "19ツール".',
        )

    def test_readme_has_19(self):
        """README.md Japanese feature table must say 19ツール."""
        self.assertIn(
            "19ツール",
            self._readme(),
            'README.md Japanese table must contain "19ツール".',
        )

    def test_claude_md_not_18(self):
        """CLAUDE.md must not advertise stale 18-tool count."""
        self.assertNotIn(
            "18 tools",
            self._claude_md(),
            'CLAUDE.md still says "18 tools" — update to "19 tools".',
        )

    def test_claude_md_has_19(self):
        """CLAUDE.md map section must advertise 19 tools."""
        self.assertIn(
            "19 tools",
            self._claude_md(),
            'CLAUDE.md must contain "19 tools" in mcp_server line.',
        )


if __name__ == "__main__":
    unittest.main()
