"""Pass 32 regression tests: runtime CLAUDE.md model-count stale (26→34)."""

import pathlib
import unittest


class TestModelDbCount(unittest.TestCase):
    """MODELS list in recommend.py must stay in sync with documentation."""

    # Count history: 26 -> 34 (Pass 32) -> 37 (Pass 184: GLM-5.2 ollama+vllm,
    # Kimi K2.6). The point of this file is that the DB and the docs move
    # TOGETHER -- update both, then this pin, in the same commit.
    EXPECTED = 37

    def test_actual_model_count(self):
        """recommend.MODELS must contain exactly EXPECTED entries."""
        from aictl.runtime.recommend import MODELS
        self.assertEqual(
            len(MODELS), self.EXPECTED,
            f"Expected {self.EXPECTED} models in MODELS list, got {len(MODELS)} — "
            "update aictl/runtime/CLAUDE.md and this pin together.",
        )

    def test_runtime_claude_md_not_stale(self):
        """aictl/runtime/CLAUDE.md must not carry a superseded count."""
        src = (
            pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "CLAUDE.md"
        ).read_text()
        for stale in ("26 models", "34 models"):
            self.assertNotIn(
                stale, src,
                f'aictl/runtime/CLAUDE.md still says "{stale}" — update to '
                f'"{self.EXPECTED} models".',
            )

    def test_runtime_claude_md_matches_pin(self):
        """aictl/runtime/CLAUDE.md must state the current count."""
        src = (
            pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "CLAUDE.md"
        ).read_text()
        self.assertIn(
            f"{self.EXPECTED} models",
            src,
            f'aictl/runtime/CLAUDE.md must contain "{self.EXPECTED} models in DB".',
        )


if __name__ == "__main__":
    unittest.main()
