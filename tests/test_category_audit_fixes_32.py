"""Pass 32 regression tests: runtime CLAUDE.md model-count stale (26→34)."""

import pathlib
import unittest


class TestModelDbCount(unittest.TestCase):
    """MODELS list in recommend.py must stay in sync with documentation."""

    def test_actual_model_count(self):
        """recommend.MODELS must contain exactly 34 entries."""
        from aictl.runtime.recommend import MODELS
        self.assertEqual(
            len(MODELS), 34,
            f"Expected 34 models in MODELS list, got {len(MODELS)}",
        )

    def test_runtime_claude_md_not_26(self):
        """aictl/runtime/CLAUDE.md must not say 26 models (stale count)."""
        src = (
            pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "CLAUDE.md"
        ).read_text()
        self.assertNotIn(
            "26 models",
            src,
            'aictl/runtime/CLAUDE.md still says "26 models" — update to "34 models".',
        )

    def test_runtime_claude_md_has_34(self):
        """aictl/runtime/CLAUDE.md must say 34 models."""
        src = (
            pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "CLAUDE.md"
        ).read_text()
        self.assertIn(
            "34 models",
            src,
            'aictl/runtime/CLAUDE.md must contain "34 models in DB".',
        )


if __name__ == "__main__":
    unittest.main()
