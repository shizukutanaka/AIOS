"""Pass 30 regression tests: Go port test count stale (1742+→1755+→1765+→1840+)."""

import pathlib
import unittest


class TestGoPortTestCount(unittest.TestCase):
    """go-port/cmd/aictl/main.go: test count must match actual suite size."""

    @classmethod
    def _go_src(cls) -> str:
        return (
            pathlib.Path(__file__).parent.parent
            / "go-port" / "cmd" / "aictl" / "main.go"
        ).read_text()

    def test_test_count_not_1742(self):
        """cmdInfo must not advertise the stale 1742+ count from Pass 27."""
        src = self._go_src()
        self.assertNotIn(
            '"1742+"',
            src,
            'Go port still reports "1742+" tests — update to "1840+".',
        )
        self.assertNotIn(
            "1742+",
            src,
            "Go port still reports 1742+ tests — update to 1840+.",
        )

    def test_test_count_not_1755(self):
        """cmdInfo must not advertise the stale 1755+ count."""
        src = self._go_src()
        self.assertNotIn(
            '"1755+"',
            src,
            'Go port still reports "1755+" tests — update to "1840+".',
        )

    def test_test_count_not_1765(self):
        """cmdInfo must not advertise the stale 1765+ count (now 1840+)."""
        src = self._go_src()
        self.assertNotIn(
            '"1765+"',
            src,
            'Go port still reports "1765+" tests — update to "1840+".',
        )

    def test_test_count_is_1840(self):
        """cmdInfo JSON and text output must advertise 1840+."""
        src = self._go_src()
        self.assertIn(
            '"1840+"',
            src,
            'Go port cmdInfo JSON must contain "tests": "1840+".',
        )
        self.assertIn(
            "1840+",
            src,
            "Go port cmdInfo text must advertise 1840+.",
        )


if __name__ == "__main__":
    unittest.main()
