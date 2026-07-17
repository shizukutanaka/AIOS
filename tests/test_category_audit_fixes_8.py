"""Pass 8 regression tests for correctness bugs identified by deep audit."""

import unittest
from unittest import mock


class TestSdkStructuredNoDeadTimer(unittest.TestCase):
    """sdk.py: structured() must not have a dead time.perf_counter() call."""

    def test_no_dead_perf_counter(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "sdk.py").read_text()
        # The dead standalone 'time.perf_counter()' statement must be gone.
        # A bare call on its own line (not assigned) is the bug signature.
        import re
        # Look for a line that is exactly 'time.perf_counter()' (dead expression)
        dead = re.search(r"^\s*time\.perf_counter\(\)\s*$", src, re.MULTILINE)
        self.assertIsNone(dead,
                          "sdk.py must not contain a standalone (dead) time.perf_counter() call")


class TestIsolationNeverRaises(unittest.TestCase):
    """isolation.py: detect_cpu_isolation_support must not raise on /proc/cmdline errors."""

    def test_isolcpus_handles_permission_error(self):
        from aictl.runtime import isolation

        # Simulate /proc/cmdline existing but raising on read (PermissionError)
        real_read_text = None

        def boom(self, *a, **k):
            raise PermissionError("Operation not permitted")

        with mock.patch("pathlib.Path.read_text", boom):
            # Must not raise even though read_text blows up
            result = isolation.detect_cpu_isolation_support()
        self.assertIsInstance(result, dict)
        self.assertIn("isolcpus", result)
        self.assertFalse(result["isolcpus"],
                         "isolcpus must be False when /proc/cmdline is unreadable")

    def test_isolcpus_handles_os_error(self):
        from aictl.runtime import isolation

        def boom(self, *a, **k):
            raise OSError("I/O error")

        with mock.patch("pathlib.Path.read_text", boom):
            result = isolation.detect_cpu_isolation_support()
        self.assertIsInstance(result, dict)
        self.assertFalse(result["isolcpus"])

    def test_normal_detection_returns_all_keys(self):
        from aictl.runtime import isolation
        result = isolation.detect_cpu_isolation_support()
        for key in ("cgroup_v2", "cpuset", "memory_min", "numa", "isolcpus"):
            self.assertIn(key, result)
            self.assertIsInstance(result[key], bool)


class TestCostSavingsFormula(unittest.TestCase):
    """cost.py: 3-year savings must be cloud_cost - onprem_cost (on-prem vs cloud)."""

    def test_savings_positive_when_onprem_cheaper(self):
        from aictl.core.cost import estimate_cost
        # Heavy 24h/day usage on a single GPU → on-prem typically wins over 3yr
        est = estimate_cost(gpu_type="RTX 4090", num_gpus=1, hours_per_day=24)
        # When on-prem is cheaper, savings (cloud - onprem) should be positive
        # Verify the sign convention matches the recommendation
        if est.recommendation.startswith("on-prem"):
            self.assertGreater(est.savings_3yr_usd, 0,
                               "When on-prem is recommended, 3yr savings must be positive")

    def test_savings_sign_consistency(self):
        from aictl.core.cost import estimate_cost
        est = estimate_cost(gpu_type="H100 SXM", num_gpus=1, hours_per_day=24)
        # savings = cloud_3yr - onprem_3yr; recompute and verify
        cloud_3yr = est.cloud_monthly_usd * 36
        onprem_3yr = est.onprem_hardware_usd + (est.onprem_power_monthly_usd * 36)
        expected = cloud_3yr - onprem_3yr
        self.assertAlmostEqual(est.savings_3yr_usd, expected, places=2,
                               msg="savings_3yr_usd must equal cloud_3yr - onprem_3yr")


if __name__ == "__main__":
    unittest.main()
