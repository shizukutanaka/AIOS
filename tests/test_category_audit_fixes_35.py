"""Pass 35 regression tests: cost_per_call zero-guard, eval pass rate display."""

import os
import unittest


class TestLocalCostZeroGuard(unittest.TestCase):
    """cost_per_call.py must not divide by zero when AICTL_TOKENS_PER_HOUR=0."""

    def test_zero_tokens_per_hour_does_not_crash_import(self):
        """Setting AICTL_TOKENS_PER_HOUR=0 must not raise ZeroDivisionError."""
        import importlib
        import sys
        old_val = os.environ.get("AICTL_TOKENS_PER_HOUR")
        try:
            os.environ["AICTL_TOKENS_PER_HOUR"] = "0"
            # Remove cached module so it re-evaluates the constant
            for key in list(sys.modules.keys()):
                if "cost_per_call" in key:
                    del sys.modules[key]
            # This must not raise
            from aictl.core import cost_per_call  # noqa: F401
        except ZeroDivisionError:
            self.fail(
                "ZeroDivisionError raised when AICTL_TOKENS_PER_HOUR=0; "
                "use max(1.0, ...) guard."
            )
        finally:
            if old_val is None:
                os.environ.pop("AICTL_TOKENS_PER_HOUR", None)
            else:
                os.environ["AICTL_TOKENS_PER_HOUR"] = old_val
            # Reload with default value
            for key in list(sys.modules.keys()):
                if "cost_per_call" in key:
                    del sys.modules[key]

    def test_zero_tokens_per_hour_fallback_to_one(self):
        """With AICTL_TOKENS_PER_HOUR=0, local cost per token must be finite and > 0."""
        import sys
        old_val = os.environ.get("AICTL_TOKENS_PER_HOUR")
        try:
            os.environ["AICTL_TOKENS_PER_HOUR"] = "0"
            for key in list(sys.modules.keys()):
                if "cost_per_call" in key:
                    del sys.modules[key]
            import aictl.core.cost_per_call as cpc
            self.assertGreater(cpc._LOCAL_COST_PER_TOKEN, 0)
            self.assertFalse(
                cpc._LOCAL_COST_PER_TOKEN == float("inf"),
                "_LOCAL_COST_PER_TOKEN is infinite",
            )
        finally:
            if old_val is None:
                os.environ.pop("AICTL_TOKENS_PER_HOUR", None)
            else:
                os.environ["AICTL_TOKENS_PER_HOUR"] = old_val
            for key in list(sys.modules.keys()):
                if "cost_per_call" in key:
                    del sys.modules[key]

    def test_compute_local_cost_normal(self):
        """compute() must return a valid CallCost for local inference."""
        from aictl.core.cost_per_call import compute
        result = compute("local", 100, 50, is_local=True)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result.cost_usd, 0)
        self.assertEqual(result.cost_source, "local")


class TestEvalPassRateDisplay(unittest.TestCase):
    """eval.py pass rate display must use round() not floor division."""

    def test_pass_rate_not_floor_divided(self):
        """eval.py must not use integer floor division for pass rate."""
        src = (
            __import__("pathlib").Path(__file__).parent.parent
            / "aictl" / "cmd" / "eval.py"
        ).read_text()
        self.assertNotIn(
            "passed*100//total",
            src,
            "eval.py still uses integer floor division for pass rate; use round().",
        )

    def test_pass_rate_uses_round(self):
        """eval.py must use round() for pass rate percentage."""
        src = (
            __import__("pathlib").Path(__file__).parent.parent
            / "aictl" / "cmd" / "eval.py"
        ).read_text()
        self.assertIn(
            "round(passed * 100 / total)",
            src,
            "eval.py must use round(passed * 100 / total) for pass rate display.",
        )

    def test_broker_run_docstring(self):
        """broker._run docstring must not say 'return an exit code'."""
        src = (
            __import__("pathlib").Path(__file__).parent.parent
            / "aictl" / "runtime" / "broker.py"
        ).read_text()
        self.assertNotIn(
            "return an exit code",
            src,
            "broker._run docstring still says 'return an exit code' (misleading).",
        )


if __name__ == "__main__":
    unittest.main()
