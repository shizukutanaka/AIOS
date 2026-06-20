"""Pass 118 (loop): reject sub-1 --period-days in `aictl tco`.

`tco` summary computed `usage_fraction = min(1.0, period_days / 30)` and
multiplied it by the monthly hardware depreciation. A negative --period-days
drove usage_fraction negative, yielding a NEGATIVE depreciation cost and an
inverted total TCO — `tco --period-days -30 --json` reported
`total_usd = -51.84` (a negative cost of ownership). period_days == 0 silently
zeroed the hardware cost.

Both period-consuming handlers (`run_summary`, `run_carbon`) now validate
`--period-days >= 1` via a shared `_check_period_days` helper and exit non-zero
with a clear message, matching the established physical-quantity convention
(`fit`, `lora auto-tune`, `cost forecast`, `bench`).
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout


def _args(period_days, **kw):
    base = dict(period_days=period_days, carbon_intensity=None,
                region="global", json=False)
    base.update(kw)
    return argparse.Namespace(**base)


class TestTcoPeriodValidation(unittest.TestCase):
    def test_negative_period_rejected_summary(self):
        from aictl.cmd.tco import run_summary
        self.assertEqual(run_summary(_args(-30)), 1)

    def test_zero_period_rejected_summary(self):
        from aictl.cmd.tco import run_summary
        self.assertEqual(run_summary(_args(0)), 1)

    def test_negative_period_rejected_carbon(self):
        from aictl.cmd.tco import run_carbon
        self.assertEqual(run_carbon(_args(-5)), 1)

    def test_rejected_summary_emits_no_negative_total(self):
        from aictl.cmd.tco import run_summary
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_summary(_args(-30, json=True))
        self.assertEqual(rc, 1)
        # The bug printed a JSON body with a negative total; rejection must not.
        self.assertEqual(buf.getvalue().strip(), "")

    def test_valid_period_yields_nonnegative_total(self):
        from aictl.cmd.tco import run_summary
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_summary(_args(30, json=True))
        self.assertEqual(rc, 0)
        d = json.loads(buf.getvalue())
        self.assertGreaterEqual(d["total_usd"], 0)
        self.assertGreaterEqual(d["depreciation_jpy"], 0)


if __name__ == "__main__":
    unittest.main()
