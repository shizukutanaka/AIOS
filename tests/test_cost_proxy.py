"""Tests for cost estimation, proxy, and health modules."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestCostEstimation(unittest.TestCase):
    def test_all_gpus_have_pricing(self):
        from aictl.core.cost import CLOUD_PRICING, HARDWARE_COST, POWER_WATTS
        # B200 and GB200 should be present
        self.assertIn("B200", CLOUD_PRICING)
        self.assertIn("GB200", CLOUD_PRICING)
        self.assertIn("RTX 5090", CLOUD_PRICING)
        self.assertIn("B200", HARDWARE_COST)
        self.assertIn("B200", POWER_WATTS)

    def test_b200_pricing_range(self):
        from aictl.core.cost import CLOUD_PRICING
        b200 = CLOUD_PRICING["B200"]
        # B200 should be $2-$10/hr range
        avg = sum(b200.values()) / len(b200)
        self.assertGreater(avg, 2.0)
        self.assertLess(avg, 12.0)

    def test_compare_gpus(self):
        from aictl.core.cost import compare_gpus
        results = compare_gpus()
        self.assertGreater(len(results), 5)
        # Check B200 is included
        gpu_names = [r.gpu_type for r in results]
        self.assertIn("B200", gpu_names)

    def test_compare_gpu_fields(self):
        from aictl.core.cost import compare_gpus
        results = compare_gpus()
        for r in results:
            self.assertGreater(r.cloud_monthly_usd, 0)
            self.assertGreater(r.onprem_monthly_usd, 0)
            self.assertGreater(r.break_even_months, 0)
            # cost_per_million_tokens may be 0 for GPUs that can't run 70B
            self.assertGreaterEqual(r.cost_per_million_tokens, 0)

    def test_b200_cheaper_per_token_than_h100(self):
        from aictl.core.cost import compare_gpus
        results = {r.gpu_type: r for r in compare_gpus()}
        if "B200" in results and "H100 SXM" in results:
            b200 = results["B200"]
            h100 = results["H100 SXM"]
            if b200.cost_per_million_tokens > 0 and h100.cost_per_million_tokens > 0:
                self.assertLess(b200.cost_per_million_tokens, h100.cost_per_million_tokens)

    def test_hardware_cost_ordering(self):
        from aictl.core.cost import HARDWARE_COST
        self.assertLess(HARDWARE_COST["RTX 4090"], HARDWARE_COST["A100 80GB"])
        self.assertLess(HARDWARE_COST["H100 SXM"], HARDWARE_COST["B200"])

    def test_power_watts_b200(self):
        from aictl.core.cost import POWER_WATTS
        self.assertEqual(POWER_WATTS["B200"], 1000)  # 1000W TDP
        self.assertEqual(POWER_WATTS["GB200"], 1200)


class TestProxyCompile(unittest.TestCase):
    def test_proxy_compiles(self):
        import py_compile
        py_compile.compile("aictl/daemon/proxy.py", doraise=True)
        self.assertTrue(True)  # compile succeeded without SyntaxError

    def test_proxy_imports(self):
        from aictl.daemon.proxy import ProxyHandler, serve_proxy
        self.assertTrue(callable(serve_proxy))


class TestHealthModule(unittest.TestCase):
    def test_health_imports(self):
        from aictl.runtime.health import ServiceHealth, check_http_health
        self.assertTrue(callable(check_http_health))

    def test_cli_health_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["health"])
        self.assertEqual(args.command, "health")


class TestCostCLI(unittest.TestCase):
    def test_cost_compare_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["cost", "compare"])
        self.assertEqual(args.cost_cmd, "compare")

    def test_cost_compare_runs(self):
        from aictl.cmd.cost import run_compare
        import argparse
        args = argparse.Namespace(json=False)
        result = run_compare(args)
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
