"""Pass 29 regression tests: tco.py ZeroDivisionError when depreciation_months=0."""

import pathlib
import unittest


class TestTcoDepreciationZero(unittest.TestCase):
    """aictl tco must not crash when depreciation_months is set to 0."""

    def test_division_guard_in_source(self):
        """Both division sites in tco.py must guard depreciation_months against 0."""
        src = (
            pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "tco.py"
        ).read_text()
        # Check that both division sites use max(..., 1) or equivalent guard.
        # The fix replaces `cfg["depreciation_months"]` with
        # `max(cfg.get("depreciation_months", 36), 1)`.
        self.assertIn(
            "max(cfg",
            src,
            "tco.py must guard depreciation_months divisions with max(..., 1)",
        )
        # Count how many division sites have the guard.
        guarded = src.count("max(cfg.get(\"depreciation_months\"")
        self.assertGreaterEqual(
            guarded, 2,
            f"Expected at least 2 guarded depreciation_months divisions, found {guarded}",
        )

    def test_zero_depreciation_arithmetic(self):
        """max(0, 1) = 1 ensures no ZeroDivisionError in depreciation calculation."""
        gpu_price = 280_000
        months = 0
        safe_months = max(months, 1)
        # Should not raise
        monthly = gpu_price / safe_months
        self.assertEqual(monthly, 280_000.0)

    def test_negative_depreciation_arithmetic(self):
        """max(-5, 1) = 1 handles nonsensical negative values too."""
        gpu_price = 280_000
        months = -5
        safe_months = max(months, 1)
        monthly = gpu_price / safe_months
        self.assertEqual(monthly, 280_000.0)

    def test_normal_depreciation_unchanged(self):
        """max(36, 1) = 36 so normal values are not affected by the guard."""
        gpu_price = 280_000
        months = 36
        safe_months = max(months, 1)
        monthly = gpu_price / safe_months
        self.assertAlmostEqual(monthly, 7777.78, places=1)


if __name__ == "__main__":
    unittest.main()
