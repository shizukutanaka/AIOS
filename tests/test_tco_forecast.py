"""Tests for aictl tco forecast subcommand."""

import argparse
import time
import unittest


class TestTcoForecastRegistered(unittest.TestCase):
    """forecast subcommand must be registered in tco.register()."""

    def _make_parser(self):
        from aictl.cmd.tco import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_forecast_parser_exists(self):
        parser = self._make_parser()
        args = parser.parse_args(["tco", "forecast"])
        self.assertEqual(args.func.__name__, "run_forecast")

    def test_forecast_default_days(self):
        parser = self._make_parser()
        args = parser.parse_args(["tco", "forecast"])
        self.assertEqual(args.days, 14)

    def test_forecast_custom_days(self):
        parser = self._make_parser()
        args = parser.parse_args(["tco", "forecast", "--days", "7"])
        self.assertEqual(args.days, 7)


class TestTcoForecastLogic(unittest.TestCase):
    """run_forecast() computes projections correctly."""

    def _run(self, perf_records, cfg=None, days=14, capture_json=True):
        """Helper: run run_forecast with mocked perf data, return captured JSON."""
        import os
        import json
        from unittest.mock import patch, MagicMock
        from aictl.cmd.tco import run_forecast

        default_cfg = {
            "gpu_price_jpy": 360000,
            "depreciation_months": 36,
            "gpu_watts": 350,
            "kwh_rate_jpy": 30,
        }
        if cfg:
            default_cfg.update(cfg)

        captured = []

        def fake_print_json(data):
            captured.append(data)

        args = argparse.Namespace(days=days, json=True)
        with patch("aictl.core.perf.read_recent", return_value=perf_records), \
             patch("aictl.cmd.tco._load_config", return_value=default_cfg), \
             patch("aictl.core.output.print_json", side_effect=fake_print_json):
            result = run_forecast(args)

        return result, captured[0] if captured else None

    def _make_records(self, num_days: int, cmds_per_day: int = 50):
        """Create fake PerfRecords for num_days consecutive days."""
        from aictl.core.perf import PerfRecord
        now = time.time()
        records = []
        for d in range(num_days):
            day_ts = now - (num_days - d - 1) * 86400
            for _ in range(cmds_per_day):
                records.append(PerfRecord(
                    timestamp=day_ts,
                    command="other",
                    duration_ms=100,
                    exit_code=0,
                    rss_mb_peak=100,
                ))
        return records

    def test_returns_zero_on_success(self):
        records = self._make_records(7)
        ret, _ = self._run(records, days=7)
        self.assertEqual(ret, 0)

    def test_projected_monthly_is_positive(self):
        records = self._make_records(7)
        _, data = self._run(records, days=7)
        self.assertIsNotNone(data)
        self.assertGreater(data["projected_monthly_jpy"], 0)

    def test_json_has_required_keys(self):
        records = self._make_records(7)
        _, data = self._run(records, days=7)
        for key in ("window_days", "avg_daily_jpy", "projected_monthly_jpy",
                    "trend", "trend_adjusted_monthly_jpy", "currency"):
            self.assertIn(key, data, f"Missing key: {key}")

    def test_currency_is_jpy(self):
        records = self._make_records(7)
        _, data = self._run(records, days=7)
        self.assertEqual(data["currency"], "JPY")

    def test_window_days_equals_available_data(self):
        records = self._make_records(5)
        _, data = self._run(records, days=14)  # request 14, only 5 available
        # window_days should be <= 5
        self.assertLessEqual(data["window_days"], 5)

    def test_avg_daily_times_30_equals_projected(self):
        records = self._make_records(7)
        _, data = self._run(records, days=7)
        # projected = avg_daily * 30 (within ±10 due to rounding at each step)
        expected = round(data["avg_daily_jpy"] * 30, 0)
        self.assertAlmostEqual(data["projected_monthly_jpy"], expected, delta=10)

    def test_flat_trend_when_uniform(self):
        """Uniform daily usage should produce flat trend."""
        records = self._make_records(14, cmds_per_day=100)
        _, data = self._run(records, days=14)
        self.assertEqual(data["trend"], "flat")

    def test_no_activity_returns_early(self):
        ret, data = self._run([])
        self.assertEqual(ret, 0)
        self.assertIsNone(data)  # warn() called, no json output


if __name__ == "__main__":
    unittest.main()
