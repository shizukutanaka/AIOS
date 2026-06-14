"""Pass 93 (loop, Socratic new perspective): error classification & honesty.

New lens: not just "does it return rc!=0" but "is the error message honest and
correctly classified". A user-input mistake was being reported as
"Unexpected error: ValueError … report a bug", misdirecting users to file an
issue for their own typo. Validation ValueErrors are now presented as
"Invalid input: <reason>" with an actionable next step. Also adds cost-hours
validation (negative hours produced a negative monthly cost).
"""

from __future__ import annotations

import argparse
import unittest


class TestValueErrorIsUserInput(unittest.TestCase):

    def test_value_error_classified_as_input(self):
        from aictl.core.errors import format_for_user
        msg = format_for_user(ValueError("hours per day must be in (0, 24], got -5"))
        self.assertIn("Invalid input", msg)
        self.assertIn("(0, 24]", msg)
        self.assertNotIn("Unexpected error", msg)
        self.assertNotIn("report at", msg)  # no bug-report misdirection

    def test_key_error_classified_as_input(self):
        from aictl.core.errors import format_for_user
        msg = format_for_user(KeyError("missing_field"))
        self.assertIn("Invalid input", msg)
        self.assertIn("missing_field", msg)
        self.assertNotIn("'missing_field'", msg)  # KeyError quotes stripped

    def test_genuine_error_still_reports_bug(self):
        from aictl.core.errors import format_for_user
        msg = format_for_user(RuntimeError("internal boom"))
        self.assertIn("Unexpected error", msg)
        self.assertIn("report at", msg)


class TestCostHoursValidation(unittest.TestCase):

    def test_negative_hours_rejected(self):
        from aictl.core.cost import estimate_cost
        with self.assertRaises(ValueError):
            estimate_cost("H100 SXM", hours_per_day=-5)

    def test_over_24_hours_rejected(self):
        from aictl.core.cost import estimate_cost
        with self.assertRaises(ValueError):
            estimate_cost("H100 SXM", hours_per_day=999)

    def test_zero_gpus_rejected(self):
        from aictl.core.cost import estimate_cost
        with self.assertRaises(ValueError):
            estimate_cost("H100 SXM", num_gpus=0)

    def test_valid_hours_ok_and_positive(self):
        from aictl.core.cost import estimate_cost
        est = estimate_cost("H100 SXM", hours_per_day=24)
        self.assertGreater(est.cloud_monthly_usd, 0)

    def test_negative_hours_via_cli_handler_is_input_error(self):
        # End-to-end: the cost command surfaces it as an input error, rc=1.
        from aictl.cmd.cost import run_estimate
        from aictl.core.errors import format_for_user
        args = argparse.Namespace(gpu="H100 SXM", hours=-5, gpus=1, json=False)
        try:
            run_estimate(args)
            self.fail("expected ValueError")
        except ValueError as e:
            self.assertIn("Invalid input", format_for_user(e))


if __name__ == "__main__":
    unittest.main()
