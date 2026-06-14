"""Pass 89 (loop): cost break-even must be consistent with the savings model.

Functional arithmetic bug: estimate_cost computed break-even as
hardware / (cloud_monthly - onprem_monthly), but onprem_monthly already amortizes
the hardware (hardware/36 + power), so dividing the hardware by that difference
double-counts it and overstated break-even (~2.3x: 48.5 vs 20.7 months for an
H100). savings_3yr uses the correct TCO model (hardware paid once + power*months);
break-even now uses the same model, so cumulative savings is exactly 0 at the
reported break-even month.
"""

from __future__ import annotations

import unittest


class TestCostBreakEvenConsistency(unittest.TestCase):

    def _est(self, gpu: str, hours: float = 24):
        from aictl.core.cost import estimate_cost
        return estimate_cost(gpu_type=gpu, num_gpus=1, hours_per_day=hours)

    def test_break_even_zeroes_cumulative_savings(self):
        # At the reported break-even month, cumulative (cloud - on-prem) must be ~0
        # under the same model savings_3yr uses: hardware once + power*months.
        for gpu in ["H100 SXM", "A100 80GB", "RTX 4090"]:
            est = self._est(gpu)
            if est.break_even_months <= 0:
                continue  # cloud always cheaper for this GPU
            m = est.break_even_months
            cumulative = est.cloud_monthly_usd * m - (
                est.onprem_hardware_usd + est.onprem_power_monthly_usd * m)
            self.assertAlmostEqual(cumulative, 0.0, delta=1.0,
                                   msg=f"{gpu}: cumulative savings {cumulative:.2f} != 0 at break-even")

    def test_break_even_matches_closed_form(self):
        est = self._est("H100 SXM")
        expected = est.onprem_hardware_usd / (
            est.cloud_monthly_usd - est.onprem_power_monthly_usd)
        self.assertAlmostEqual(est.break_even_months, expected, places=4)

    def test_break_even_not_using_amortized_monthly(self):
        # Guard against regressing to the double-counting formula, which would give
        # a strictly larger break-even than the correct one.
        est = self._est("H100 SXM")
        wrong = est.onprem_hardware_usd / (
            est.cloud_monthly_usd - est.onprem_monthly_usd)
        self.assertLess(est.break_even_months, wrong)


if __name__ == "__main__":
    unittest.main()
