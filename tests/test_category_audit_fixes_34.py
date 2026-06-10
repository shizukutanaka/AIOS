"""Pass 34 regression tests: REST endpoint count (22→30), test/module counts stale."""

import pathlib
import unittest


class TestRestEndpointCount(unittest.TestCase):
    """aiosd.py REST endpoint count must be consistent with docs at 30."""

    @classmethod
    def _claude_md(cls) -> str:
        return (pathlib.Path(__file__).parent.parent / "CLAUDE.md").read_text()

    @classmethod
    def _readme(cls) -> str:
        return (pathlib.Path(__file__).parent.parent / "README.md").read_text()

    @classmethod
    def _go_main(cls) -> str:
        return (
            pathlib.Path(__file__).parent.parent
            / "go-port" / "cmd" / "aictl" / "main.go"
        ).read_text()

    def test_actual_get_endpoint_count(self):
        """aiosd.py must contain at least 22 GET routes."""
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "daemon" / "aiosd.py"
        ).read_text()
        # Count route dict entries in do_GET
        import re
        get_block = src[src.index("def do_GET"):src.index("def do_POST")]
        count = len(re.findall(r'"/v1/', get_block))
        self.assertGreaterEqual(count, 21,
                                f"Expected ≥21 GET /v1/ routes, got {count}")

    def test_actual_post_endpoint_count(self):
        """aiosd.py must contain at least 8 POST routes."""
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "daemon" / "aiosd.py"
        ).read_text()
        import re
        post_idx = src.index("def do_POST")
        # Find the route dict in do_POST
        post_section = src[post_idx:post_idx + 1000]
        count = len(re.findall(r'"/v1/', post_section))
        self.assertGreaterEqual(count, 8,
                                f"Expected ≥8 POST /v1/ routes, got {count}")

    def test_claude_md_not_22_rest(self):
        """CLAUDE.md must not say 'aiosd(22 REST)' (stale)."""
        self.assertNotIn(
            "aiosd(22 REST)",
            self._claude_md(),
            'CLAUDE.md still says "aiosd(22 REST)" — update to "aiosd(30 REST)".',
        )

    def test_claude_md_has_30_rest(self):
        """CLAUDE.md must say 'aiosd(30 REST)'."""
        self.assertIn(
            "aiosd(30 REST)",
            self._claude_md(),
            'CLAUDE.md must say "aiosd(30 REST)".',
        )

    def test_readme_not_22_rest(self):
        """README.md architecture section must not say '22 REST API'."""
        self.assertNotIn(
            "22 REST API",
            self._readme(),
            'README.md architecture still says "22 REST API" — update to "30 REST API".',
        )

    def test_readme_has_30_rest(self):
        """README.md architecture section must say '30 REST API'."""
        self.assertIn(
            "30 REST API",
            self._readme(),
            'README.md architecture must say "30 REST API".',
        )

    def test_go_port_not_22_rest(self):
        """go-port main.go must not have rest_endpoints: 22."""
        self.assertNotIn(
            '"rest_endpoints":  22',
            self._go_main(),
            'go-port/cmd/aictl/main.go still has rest_endpoints: 22.',
        )

    def test_go_port_has_30_rest(self):
        """go-port main.go must have rest_endpoints: 30."""
        self.assertIn(
            '"rest_endpoints":  30',
            self._go_main(),
            'go-port/cmd/aictl/main.go must have rest_endpoints: 30.',
        )


class TestReadmeTestCount(unittest.TestCase):
    """README.md test and module counts must reflect current reality."""

    @classmethod
    def _readme(cls) -> str:
        return (pathlib.Path(__file__).parent.parent / "README.md").read_text()

    def test_readme_not_1380_tests(self):
        """README.md must not advertise stale 1380 test count."""
        self.assertNotIn(
            "1380 tests",
            self._readme(),
            'README.md still says "1380 tests" — update to current count.',
        )

    def test_readme_has_current_test_count(self):
        """README.md stat line must have ≥1776 tests."""
        import re
        src = self._readme()
        m = re.search(r"(\d+) tests \|", src)
        self.assertIsNotNone(m, "README.md stat line must contain 'N tests |'")
        count = int(m.group(1))
        self.assertGreaterEqual(
            count, 1776,
            f"README.md test count {count} is below expected 1776",
        )

    def test_changelog_not_1380(self):
        """CHANGELOG.md must not reference 1380 test count."""
        src = (pathlib.Path(__file__).parent.parent / "CHANGELOG.md").read_text()
        self.assertNotIn(
            "1380 tests",
            src,
            'CHANGELOG.md still says "1380 tests" — update to current count.',
        )


if __name__ == "__main__":
    unittest.main()
