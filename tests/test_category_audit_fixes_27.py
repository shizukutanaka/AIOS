"""Pass 27 regression tests: Go port version mismatch (1.4.0→1.6.0); stale test count."""

import pathlib
import re
import unittest


class TestGoPortVersion(unittest.TestCase):
    """go-port/cmd/aictl/main.go: version variable must match the project release."""

    @classmethod
    def _go_src(cls) -> str:
        return (
            pathlib.Path(__file__).parent.parent
            / "go-port" / "cmd" / "aictl" / "main.go"
        ).read_text()

    def test_version_var_is_not_stale(self):
        """The `version` package-level var must be 1.6.0, not the old 1.4.0 value."""
        src = self._go_src()
        m = re.search(r'version\s*=\s*"([^"]+)"', src)
        self.assertIsNotNone(m, "version variable not found in main.go")
        self.assertEqual(
            m.group(1), "1.6.0",
            f"Go port version should be 1.6.0, got '{m.group(1)}'. "
            "Update the `version` var in go-port/cmd/aictl/main.go.",
        )

    def test_version_not_1_4_0(self):
        """Explicit guard: 1.4.0 must not appear as the binary version."""
        src = self._go_src()
        m = re.search(r'version\s*=\s*"([^"]+)"', src)
        if m:
            self.assertNotEqual(
                m.group(1), "1.4.0",
                "Go port `version` is still 1.4.0 — `aictl --version` would lie.",
            )

    def test_cmdinfo_json_version_matches(self):
        """cmdInfo JSON output must advertise 1.6.0."""
        src = self._go_src()
        info_section = re.search(
            r'func cmdInfo\(\).*?^}', src, re.MULTILINE | re.DOTALL
        )
        self.assertIsNotNone(info_section, "cmdInfo function not found in main.go")
        body = info_section.group(0)
        self.assertIn(
            '"version":         "1.6.0"',
            body,
            "cmdInfo JSON block must set version to 1.6.0",
        )

    def test_cmdinfo_test_count_not_stale(self):
        """cmdInfo must not advertise the old 1695+ test count."""
        src = self._go_src()
        self.assertNotIn(
            '"1695+"',
            src,
            "Go port still reports 1695+ tests — update to 1742+.",
        )


if __name__ == "__main__":
    unittest.main()
