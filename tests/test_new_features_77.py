"""Pass 77 regression tests: fabric migrate/monitor/damon, lora route/auto-tune, spec profile/export."""

from __future__ import annotations

import argparse
import pathlib
import tempfile
import unittest
from unittest.mock import patch, MagicMock


# ── fabric: migrate / monitor / damon ────────────────────────────────────────

class TestFabricNewSubcommands(unittest.TestCase):
    """fabric migrate, monitor, damon subcommands."""

    def _make_parser(self):
        from aictl.cmd.fabric import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_migrate_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["fabric", "migrate", "llama3"])
        self.assertEqual(args.func.__name__, "run_migrate")
        self.assertEqual(args.model, "llama3")

    def test_monitor_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["fabric", "monitor"])
        self.assertEqual(args.func.__name__, "run_monitor")

    def test_damon_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["fabric", "damon", "1234"])
        self.assertEqual(args.func.__name__, "run_damon")
        self.assertEqual(args.pid, 1234)

    def test_migrate_pid_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["fabric", "migrate", "llama3", "--pid", "5678"])
        self.assertEqual(args.pid, 5678)

    def test_run_migrate_json(self):
        from aictl.cmd.fabric import run_migrate
        from aictl.runtime.fabric import FabricReport, MemoryTier, PlacementPolicy
        fake_report = FabricReport(
            tiers=[MemoryTier("dram", 32.0, 50.0, 80.0, 10.0)],
            damon_available=False,
        )
        captured = []
        with patch("aictl.cmd.fabric.detect_memory_fabric", return_value=fake_report), \
             patch("aictl.cmd.fabric.generate_placement_policy",
                   return_value=PlacementPolicy()), \
             patch("aictl.cmd.fabric.print_json", side_effect=captured.append):
            args = argparse.Namespace(model="llama3", pid=0, json=True)
            ret = run_migrate(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["model"], "llama3")
        self.assertIn("hints", captured[0])
        self.assertGreater(len(captured[0]["hints"]), 0)

    def test_run_monitor_json(self):
        from aictl.cmd.fabric import run_monitor
        from aictl.runtime.fabric import FabricReport, MemoryTier
        from aictl.metrics.slo import SystemPressure
        fake_report = FabricReport(
            tiers=[MemoryTier("dram", 32.0, 50.0, 80.0, 10.0)],
        )
        fake_psi = SystemPressure(memory_some_avg10=5.0, memory_some_avg60=3.0,
                                   cpu_some_avg10=1.0, io_some_avg10=0.5)
        captured = []
        with patch("aictl.cmd.fabric.detect_memory_fabric", return_value=fake_report), \
             patch("aictl.cmd.fabric.read_psi", return_value=fake_psi), \
             patch("aictl.cmd.fabric.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True)
            ret = run_monitor(args)
        self.assertEqual(ret, 0)
        self.assertIn("pressure", captured[0])
        self.assertEqual(captured[0]["pressure"]["memory_some_avg10"], 5.0)

    def test_run_damon_json(self):
        from aictl.cmd.fabric import run_damon
        captured = []
        with patch("aictl.cmd.fabric.print_json", side_effect=captured.append):
            args = argparse.Namespace(pid=9999, sample_us=5000, json=True)
            ret = run_damon(args)
        self.assertEqual(ret, 0)
        self.assertIn("sysfs_writes", captured[0])
        self.assertIn("notes", captured[0])
        self.assertGreater(len(captured[0]["sysfs_writes"]), 0)

    def test_run_damon_contains_pid(self):
        from aictl.cmd.fabric import run_damon
        captured = []
        with patch("aictl.cmd.fabric.print_json", side_effect=captured.append):
            args = argparse.Namespace(pid=42, sample_us=5000, json=True)
            run_damon(args)
        desc = captured[0]["description"]
        self.assertIn("42", desc)


# ── lora: route / auto-tune ──────────────────────────────────────────────────

class TestLoraRoutingAutotune(unittest.TestCase):
    """lora route and auto-tune subcommands."""

    def _make_parser(self):
        from aictl.cmd.lora import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_route_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["lora", "route", "finance-lora", "--weight", "30"])
        self.assertEqual(args.func.__name__, "run_route")
        self.assertEqual(args.weight, 30)

    def test_autotune_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["lora", "auto-tune", "llama3"])
        self.assertEqual(args.func.__name__, "run_autotune")
        self.assertEqual(args.base, "llama3")

    def test_run_route_success(self):
        from aictl.cmd.lora import run_route
        data = {"adapters": {"finance": {"name": "finance", "base_model": "llama3",
                                          "path": "", "vram_overhead_mb": 100,
                                          "rank": 16, "active": True, "traffic_weight": 100}},
                "bases": {}}
        saved = []
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            inst = MockMgr.return_value
            inst._load.return_value = data
            inst._save.side_effect = saved.append
            args = argparse.Namespace(name="finance", weight=30, json=False)
            ret = run_route(args)
        self.assertEqual(ret, 0)
        self.assertEqual(saved[0]["adapters"]["finance"]["traffic_weight"], 30)

    def test_run_route_clamps_weight(self):
        from aictl.cmd.lora import run_route
        data = {"adapters": {"lora1": {"name": "lora1", "base_model": "x",
                                        "path": "", "vram_overhead_mb": 100,
                                        "rank": 8, "active": True, "traffic_weight": 100}},
                "bases": {}}
        saved = []
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            inst = MockMgr.return_value
            inst._load.return_value = data
            inst._save.side_effect = saved.append
            args = argparse.Namespace(name="lora1", weight=200, json=False)
            run_route(args)
        # weight must be clamped to 100
        self.assertEqual(saved[0]["adapters"]["lora1"]["traffic_weight"], 100)

    def test_run_route_not_found(self):
        from aictl.cmd.lora import run_route
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            MockMgr.return_value._load.return_value = {"adapters": {}, "bases": {}}
            args = argparse.Namespace(name="ghost", weight=50, json=False)
            ret = run_route(args)
        self.assertEqual(ret, 1)

    def test_run_route_json(self):
        from aictl.cmd.lora import run_route
        data = {"adapters": {"lora-x": {"name": "lora-x", "base_model": "m",
                                         "path": "", "vram_overhead_mb": 50,
                                         "rank": 8, "active": True, "traffic_weight": 100}},
                "bases": {}}
        captured = []
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            inst = MockMgr.return_value
            inst._load.return_value = data
            inst._save.side_effect = lambda d: None
            with patch("aictl.cmd.lora.print_json", side_effect=captured.append):
                args = argparse.Namespace(name="lora-x", weight=60, json=True)
                run_route(args)
        self.assertEqual(captured[0]["traffic_weight"], 60)

    def test_run_autotune_fits_all(self):
        from aictl.cmd.lora import run_autotune
        from aictl.runtime.lora import LoRAAdapter
        adapters = [
            LoRAAdapter("a1", "llama3", rank=8, vram_overhead_mb=100, active=True, traffic_weight=80),
            LoRAAdapter("a2", "llama3", rank=16, vram_overhead_mb=150, active=True, traffic_weight=40),
        ]
        captured = []
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            MockMgr.return_value.list_adapters.return_value = adapters
            with patch("aictl.cmd.lora.print_json", side_effect=captured.append):
                args = argparse.Namespace(base="llama3", vram=24, json=True)
                ret = run_autotune(args)
        self.assertEqual(ret, 0)
        # Both fit in 24GB
        self.assertEqual(len(captured[0]["keep"]), 2)
        self.assertEqual(len(captured[0]["evict"]), 0)

    def test_run_autotune_evicts_low_traffic(self):
        from aictl.cmd.lora import run_autotune
        from aictl.runtime.lora import LoRAAdapter
        # 600MB per adapter, budget = 1 GB (1024 MB) → only 1 fits (600 < 1024, 1200 > 1024)
        adapters = [
            LoRAAdapter("high", "llama3", rank=8, vram_overhead_mb=600, active=True, traffic_weight=90),
            LoRAAdapter("low",  "llama3", rank=8, vram_overhead_mb=600, active=True, traffic_weight=10),
        ]
        captured = []
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            MockMgr.return_value.list_adapters.return_value = adapters
            with patch("aictl.cmd.lora.print_json", side_effect=captured.append):
                args = argparse.Namespace(base="llama3", vram=1, json=True)
                ret = run_autotune(args)
        self.assertEqual(ret, 0)
        self.assertIn("high", captured[0]["keep"])
        self.assertIn("low", captured[0]["evict"])


# ── spec: profile / export ───────────────────────────────────────────────────

class TestSpecProfileExport(unittest.TestCase):
    """spec profile and spec export subcommands."""

    def _make_parser(self):
        from aictl.cmd.spec import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_profile_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["spec", "profile", "llama3.1:70b"])
        self.assertEqual(args.func.__name__, "run_profile")
        self.assertEqual(args.target, "llama3.1:70b")

    def test_export_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["spec", "export", "llama3.1:70b"])
        self.assertEqual(args.func.__name__, "run_export")

    def test_profile_requests_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["spec", "profile", "llama3.1:70b", "-n", "10"])
        self.assertEqual(args.requests, 10)

    def test_run_profile_json(self):
        from aictl.cmd.spec import run_profile
        from aictl.runtime.benchmark import BenchResult
        mock_result = BenchResult(
            endpoint="http://localhost:8000", model="llama3.1:70b",
            requests=5, errors=0,
            ttft_ms_avg=200.0, ttft_ms_p95=350.0,
            tokens_per_sec=30.0, total_ms_avg=800.0,
            duration_sec=5.0, tokens_generated=150,
        )
        captured = []
        with patch("aictl.cmd.spec.run_benchmark", return_value=mock_result), \
             patch("aictl.core.output.print_json", side_effect=captured.append):
            args = argparse.Namespace(
                target="llama3.1:70b", draft="llama3.2:1b",
                endpoint="http://localhost:8000", requests=5,
            )
            args.__dict__["json"] = True
            ret = run_profile(args)
        self.assertEqual(ret, 0)
        d = captured[0]
        self.assertEqual(d["target_model"], "llama3.1:70b")
        self.assertGreater(d["estimated_speedup"], 1.0)
        self.assertGreater(d["estimated_acceptance_rate"], 0.5)

    def test_run_profile_uses_known_acceptance(self):
        """Profile picks known acceptance rate from PAIRS for llama3.1:70b + llama3.2:1b."""
        from aictl.cmd.spec import run_profile, PAIRS
        from aictl.runtime.benchmark import BenchResult
        pair = next(p for p in PAIRS if p.target == "llama3.1:70b" and p.draft == "llama3.2:1b")
        mock_result = BenchResult(requests=5, errors=0, tokens_per_sec=20.0)
        captured = []
        with patch("aictl.cmd.spec.run_benchmark", return_value=mock_result), \
             patch("aictl.core.output.print_json", side_effect=captured.append):
            args = argparse.Namespace(
                target="llama3.1:70b", draft="llama3.2:1b",
                endpoint="http://localhost:8000", requests=5,
            )
            args.__dict__["json"] = True
            run_profile(args)
        # Should use table acceptance_rate, not generic 0.80
        self.assertAlmostEqual(
            captured[0]["estimated_acceptance_rate"], pair.acceptance_rate, places=2)

    def test_run_profile_error(self):
        from aictl.cmd.spec import run_profile
        with patch("aictl.cmd.spec.run_benchmark", side_effect=ConnectionRefusedError):
            args = argparse.Namespace(
                target="llama3.1:70b", draft="", endpoint="http://localhost:1", requests=1)
            args.__dict__["json"] = False
            ret = run_profile(args)
        self.assertEqual(ret, 1)

    def test_run_export_json(self):
        from aictl.cmd.spec import run_export
        captured = []
        with patch("aictl.core.output.print_json", side_effect=captured.append):
            args = argparse.Namespace(target="llama3.1:70b", draft="llama3.2:1b")
            args.__dict__["json"] = True
            ret = run_export(args)
        self.assertEqual(ret, 0)
        self.assertGreater(captured[0]["acceptance_rate"], 0.5)
        self.assertGreater(captured[0]["estimated_speedup"], 1.0)
        self.assertIn("gamma", captured[0])

    def test_run_export_prometheus_format(self):
        """Export produces valid Prometheus text with expected metric names."""
        from aictl.cmd.spec import run_export
        import io, sys
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            args = argparse.Namespace(target="llama3.1:70b", draft="llama3.2:1b")
            args.__dict__["json"] = False
            run_export(args)
        finally:
            sys.stdout = old
        output = buf.getvalue()
        self.assertIn("aios_spec_acceptance_rate", output)
        self.assertIn("aios_spec_speedup_ratio", output)
        self.assertIn("aios_spec_gamma", output)

    def test_run_export_unknown_model_fallback(self):
        from aictl.cmd.spec import run_export
        captured = []
        with patch("aictl.core.output.print_json", side_effect=captured.append):
            args = argparse.Namespace(target="unknown-model:99b", draft="")
            args.__dict__["json"] = True
            ret = run_export(args)
        self.assertEqual(ret, 0)
        # Should use default 0.80 acceptance rate
        self.assertEqual(captured[0]["acceptance_rate"], 0.80)


if __name__ == "__main__":
    unittest.main()
