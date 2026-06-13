"""Pass 73 regression tests: config validate/diff, optimize advisor."""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch, MagicMock


# ── config validate ───────────────────────────────────────────────────────────

class TestConfigValidate(unittest.TestCase):
    """config validate — checks config for common errors."""

    def _make_parser(self):
        from aictl.cmd.config import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_validate_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["config", "validate"])
        self.assertEqual(args.func.__name__, "run_validate")

    def test_valid_config_returns_0(self):
        from aictl.cmd.config import run_validate
        from aictl.core.config import Config
        with patch("aictl.cmd.config.load_config", return_value=Config()):
            args = argparse.Namespace(state_dir=None, json=False)
            ret = run_validate(args)
        self.assertEqual(ret, 0)

    def test_valid_config_json(self):
        from aictl.cmd.config import run_validate
        from aictl.core.config import Config
        captured = []
        with patch("aictl.cmd.config.load_config", return_value=Config()), \
             patch("aictl.cmd.config.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=None, json=True)
            ret = run_validate(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["valid"])
        self.assertEqual(captured[0]["problems"], [])

    def test_invalid_trust_policy(self):
        from aictl.cmd.config import _validate_config
        from aictl.core.config import Config
        c = Config(trust_policy="unknown")
        problems = _validate_config(c)
        self.assertTrue(any("trust_policy" in p for p in problems))

    def test_invalid_log_level(self):
        from aictl.cmd.config import _validate_config
        from aictl.core.config import Config
        c = Config(log_level="verbose")
        problems = _validate_config(c)
        self.assertTrue(any("log_level" in p for p in problems))

    def test_invalid_daemon_port(self):
        from aictl.cmd.config import _validate_config
        from aictl.core.config import Config, DaemonConfig
        c = Config(daemon=DaemonConfig(port=99999))
        problems = _validate_config(c)
        self.assertTrue(any("daemon.port" in p for p in problems))

    def test_invalid_engine_url(self):
        from aictl.cmd.config import _validate_config
        from aictl.core.config import Config, EngineEndpoints
        c = Config(engines=EngineEndpoints(vllm="not-a-url"))
        problems = _validate_config(c)
        self.assertTrue(any("engines.vllm" in p for p in problems))

    def test_invalid_slo_ttft(self):
        from aictl.cmd.config import _validate_config
        from aictl.core.config import Config, SLOConfig
        c = Config(slo=SLOConfig(ttft_p95_ms=-1.0))
        problems = _validate_config(c)
        self.assertTrue(any("ttft_p95_ms" in p for p in problems))

    def test_invalid_slo_error_rate(self):
        from aictl.cmd.config import _validate_config
        from aictl.core.config import Config, SLOConfig
        c = Config(slo=SLOConfig(error_rate_max=1.5))
        problems = _validate_config(c)
        self.assertTrue(any("error_rate_max" in p for p in problems))

    def test_invalid_config_returns_1(self):
        from aictl.cmd.config import run_validate
        from aictl.core.config import Config
        captured = []
        with patch("aictl.cmd.config.load_config", return_value=Config(trust_policy="bogus")), \
             patch("aictl.cmd.config.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=None, json=True)
            ret = run_validate(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["valid"])
        self.assertTrue(len(captured[0]["problems"]) > 0)


# ── config diff ───────────────────────────────────────────────────────────────

class TestConfigDiff(unittest.TestCase):
    """config diff — shows keys that differ from defaults."""

    def _make_parser(self):
        from aictl.cmd.config import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_diff_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["config", "diff"])
        self.assertEqual(args.func.__name__, "run_diff")

    def test_diff_no_changes(self):
        from aictl.cmd.config import run_diff
        from aictl.core.config import Config
        with patch("aictl.cmd.config.load_config", return_value=Config()):
            args = argparse.Namespace(state_dir=None, json=False)
            ret = run_diff(args)
        self.assertEqual(ret, 0)

    def test_diff_shows_changed_keys_json(self):
        from aictl.cmd.config import run_diff
        from aictl.core.config import Config
        captured = []
        modified = Config(log_level="debug", trust_policy="enforce")
        with patch("aictl.cmd.config.load_config", return_value=modified), \
             patch("aictl.cmd.config.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=None, json=True)
            ret = run_diff(args)
        self.assertEqual(ret, 0)
        keys = {d["key"] for d in captured[0]}
        self.assertIn("log_level", keys)
        self.assertIn("trust_policy", keys)

    def test_diff_shows_correct_values(self):
        from aictl.cmd.config import run_diff
        from aictl.core.config import Config
        captured = []
        modified = Config(log_level="debug")
        with patch("aictl.cmd.config.load_config", return_value=modified), \
             patch("aictl.cmd.config.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=None, json=True)
            run_diff(args)
        entry = next(d for d in captured[0] if d["key"] == "log_level")
        self.assertEqual(entry["current"], "debug")
        self.assertEqual(entry["default"], "info")


# ── optimize ──────────────────────────────────────────────────────────────────

class TestOptimizeCommand(unittest.TestCase):
    """aictl optimize — inference performance tuning advisor."""

    def _make_parser(self):
        from aictl.cmd.optimize import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_optimize_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["optimize"])
        self.assertEqual(args.func.__name__, "run")

    def test_engine_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["optimize", "--engine", "vllm"])
        self.assertEqual(args.engine, "vllm")

    def test_top_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["optimize", "--top", "3"])
        self.assertEqual(args.top, 3)

    def test_run_no_engines(self):
        from aictl.cmd.optimize import run
        with patch("aictl.cmd.optimize.discover_engines", return_value=[]):
            args = argparse.Namespace(engine="", top=5, state_dir=None, json=False)
            ret = run(args)
        self.assertEqual(ret, 0)

    def test_run_json_no_engines(self):
        from aictl.cmd.optimize import run
        captured = []
        with patch("aictl.cmd.optimize.discover_engines", return_value=[]), \
             patch("aictl.cmd.optimize.print_json", side_effect=captured.append):
            args = argparse.Namespace(engine="", top=5, state_dir=None, json=True)
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0], [])

    def _make_metrics(self, ttft=200.0, itl=20.0, kv=0.5, queue=0, throughput=50.0):
        m = MagicMock()
        m.ttft_ms_p95 = ttft
        m.itl_ms_p95 = itl
        m.kv_cache_utilization = kv
        m.queue_depth = queue
        m.throughput_tokens_per_sec = throughput
        return m

    def test_analyze_engine_no_issues(self):
        from aictl.cmd.optimize import _analyze_engine
        from aictl.core.config import SLOConfig
        slo = SLOConfig()
        adapter = MagicMock()
        adapter.health.return_value = MagicMock(reachable=True)
        adapter.scrape_metrics.return_value = self._make_metrics()
        with patch("aictl.cmd.optimize.get_adapter", return_value=adapter):
            recs = _analyze_engine("vllm", "http://localhost:8000", slo)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["impact"], "low")

    def test_analyze_engine_high_ttft_yields_recommendation(self):
        from aictl.cmd.optimize import _analyze_engine
        from aictl.core.config import SLOConfig
        slo = SLOConfig(ttft_p95_ms=200.0)
        adapter = MagicMock()
        adapter.health.return_value = MagicMock(reachable=True)
        adapter.scrape_metrics.return_value = self._make_metrics(ttft=800.0)
        with patch("aictl.cmd.optimize.get_adapter", return_value=adapter):
            recs = _analyze_engine("vllm", "http://localhost:8000", slo)
        # should have at least one high-impact recommendation
        self.assertTrue(any(r["impact"] == "high" for r in recs))
        self.assertTrue(any("quant" in r["recommendation"].lower() or
                             "INT4" in r["recommendation"] for r in recs))

    def test_analyze_engine_high_kv_yields_scale_recommendation(self):
        from aictl.cmd.optimize import _analyze_engine
        from aictl.core.config import SLOConfig
        slo = SLOConfig(kv_cache_max=0.9)
        adapter = MagicMock()
        adapter.health.return_value = MagicMock(reachable=True)
        adapter.scrape_metrics.return_value = self._make_metrics(kv=0.95)
        with patch("aictl.cmd.optimize.get_adapter", return_value=adapter):
            recs = _analyze_engine("vllm", "http://localhost:8000", slo)
        self.assertTrue(any("scale" in r["recommendation"].lower() or
                             "replica" in r["recommendation"].lower() for r in recs))

    def test_analyze_engine_unreachable(self):
        from aictl.cmd.optimize import _analyze_engine
        from aictl.core.config import SLOConfig
        slo = SLOConfig()
        adapter = MagicMock()
        adapter.health.return_value = MagicMock(reachable=False)
        with patch("aictl.cmd.optimize.get_adapter", return_value=adapter):
            recs = _analyze_engine("vllm", "http://localhost:8000", slo)
        self.assertEqual(recs, [])

    def test_run_json_high_ttft(self):
        from aictl.cmd.optimize import run
        from aictl.runtime.adapters import EngineHealth
        health = EngineHealth(engine="vllm", endpoint="http://localhost:8000", reachable=True)
        adapter = MagicMock()
        adapter.health.return_value = MagicMock(reachable=True)
        adapter.scrape_metrics.return_value = self._make_metrics(ttft=1000.0)
        captured = []
        with patch("aictl.cmd.optimize.discover_engines", return_value=[health]), \
             patch("aictl.cmd.optimize.get_adapter", return_value=adapter), \
             patch("aictl.cmd.optimize.print_json", side_effect=captured.append):
            args = argparse.Namespace(engine="", top=5, state_dir=None, json=True)
            ret = run(args)
        self.assertEqual(ret, 0)
        recs = captured[0]
        self.assertGreater(len(recs), 0)
        impacts = [r["impact"] for r in recs]
        # High TTFT should produce high-impact recommendations first
        self.assertEqual(impacts[0], "high")

    def test_optimize_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["optimize"])
        self.assertEqual(args.func.__name__, "run")


if __name__ == "__main__":
    unittest.main()
