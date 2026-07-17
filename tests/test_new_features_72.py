"""Pass 72 regression tests: ps --extended, scale status, meter report."""

from __future__ import annotations

import argparse
import time
import unittest
from unittest.mock import patch, MagicMock


# ── ps --extended ──────────────────────────────────────────────────────────────

class TestPsExtended(unittest.TestCase):
    """ps --extended shows CPU/memory resource usage."""

    def _make_parser(self):
        from aictl.cmd.ps import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_extended_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["ps", "--extended"])
        self.assertTrue(args.extended)

    def test_extended_default_false(self):
        parser = self._make_parser()
        args = parser.parse_args(["ps"])
        self.assertFalse(args.extended)

    def test_run_no_services(self):
        from aictl.cmd.ps import run
        with patch("aictl.cmd.ps.list_running", return_value=[]), \
             patch("aictl.cmd.ps.StateStore") as MockStore:
            MockStore.return_value.load_stacks.return_value = []
            args = argparse.Namespace(stack="", extended=False,
                                      state_dir=None, json=False)
            ret = run(args)
        self.assertEqual(ret, 0)

    def test_run_extended_adds_cpu_mem_columns(self):
        from aictl.cmd.ps import run
        services = [{"name": "aios-chat", "status": "running",
                     "ports": "8000->8000/tcp", "container_id": "abc123"}]
        fake_stats = {"aios-chat": {"cpu": "12.5", "mem": "2.1GB / 8GB"}}
        captured = []
        with patch("aictl.cmd.ps.list_running", return_value=services), \
             patch("aictl.cmd.ps._fetch_stats", return_value=fake_stats), \
             patch("aictl.cmd.ps.StateStore") as MockStore, \
             patch("aictl.cmd.ps.print_json", side_effect=captured.append):
            MockStore.return_value.load_stacks.return_value = []
            args = argparse.Namespace(stack="", extended=True,
                                      state_dir=None, json=True)
            ret = run(args)
        self.assertEqual(ret, 0)
        svc = captured[0]["services"][0]
        self.assertEqual(svc["cpu%"], "12.5")
        self.assertEqual(svc["mem"], "2.1GB / 8GB")

    def test_fetch_stats_no_runtime(self):
        from aictl.cmd.ps import _fetch_stats
        with patch("aictl.cmd.ps.detect_container_runtime", return_value="none"):
            result = _fetch_stats(["aios-chat"])
        self.assertEqual(result, {})

    def test_fetch_stats_parses_json(self):
        from aictl.cmd.ps import _fetch_stats
        import json
        fake_json = json.dumps([{"Name": "aios-chat", "CPUPerc": "5%",
                                  "MemUsage": "512MB / 4GB"}])
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = fake_json
        with patch("aictl.cmd.ps.detect_container_runtime", return_value="podman"), \
             patch("subprocess.run", return_value=fake_proc):
            result = _fetch_stats(["aios-chat"])
        self.assertIn("aios-chat", result)
        self.assertEqual(result["aios-chat"]["cpu"], "5")
        self.assertEqual(result["aios-chat"]["mem"], "512MB / 4GB")


# ── scale status ──────────────────────────────────────────────────────────────

class TestScaleStatus(unittest.TestCase):
    """scale status shows live autoscaling decisions."""

    def _make_parser(self):
        from aictl.cmd.scale import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_status_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["scale", "status"])
        self.assertEqual(args.func.__name__, "run_status")

    def test_engine_filter_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["scale", "status", "--engine", "vllm"])
        self.assertEqual(args.engine, "vllm")

    def test_run_status_json(self):
        from aictl.cmd.scale import run_status
        from aictl.runtime.adapters import EngineHealth
        from aictl.runtime.autoscaler import ScaleDecision

        health = EngineHealth(engine="vllm", endpoint="http://localhost:8000",
                              reachable=True, status="READY")
        decision = ScaleDecision(action="none", current_replicas=2, desired_replicas=2,
                                 reason="balanced", metrics={"queue_depth": 0.0})

        captured = []
        with patch("aictl.cmd.scale.discover_engines", return_value=[health]), \
             patch("aictl.cmd.scale.AutoScaler") as MockScaler, \
             patch("aictl.cmd.scale.print_json", side_effect=captured.append):
            MockScaler.return_value.evaluate.return_value = decision
            args = argparse.Namespace(engine="", json=True)
            ret = run_status(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 1)
        r = captured[0][0]
        self.assertEqual(r["engine"], "vllm")
        self.assertEqual(r["action"], "none")
        self.assertEqual(r["current_replicas"], 2)
        self.assertEqual(r["reason"], "balanced")

    def test_run_status_engine_filter(self):
        from aictl.cmd.scale import run_status
        from aictl.runtime.adapters import EngineHealth
        from aictl.runtime.autoscaler import ScaleDecision

        healths = [
            EngineHealth(engine="vllm", endpoint="http://localhost:8000", reachable=True),
            EngineHealth(engine="ollama", endpoint="http://localhost:11434", reachable=False),
        ]
        decision = ScaleDecision()
        captured = []
        with patch("aictl.cmd.scale.discover_engines", return_value=healths), \
             patch("aictl.cmd.scale.AutoScaler") as MockScaler, \
             patch("aictl.cmd.scale.print_json", side_effect=captured.append):
            MockScaler.return_value.evaluate.return_value = decision
            args = argparse.Namespace(engine="vllm", json=True)
            ret = run_status(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 1)
        self.assertEqual(captured[0][0]["engine"], "vllm")

    def test_run_status_no_engines(self):
        from aictl.cmd.scale import run_status
        with patch("aictl.cmd.scale.discover_engines", return_value=[]):
            args = argparse.Namespace(engine="", json=False)
            ret = run_status(args)
        self.assertEqual(ret, 0)

    def test_run_status_scale_up_action(self):
        from aictl.cmd.scale import run_status
        from aictl.runtime.adapters import EngineHealth
        from aictl.runtime.autoscaler import ScaleDecision

        health = EngineHealth(engine="vllm", endpoint="http://localhost:8000", reachable=True)
        decision = ScaleDecision(
            action="scale_up", current_replicas=1, desired_replicas=2,
            reason="queue=8>5",
            metrics={"queue_depth": 8.0, "kv_cache_util": 0.5, "active_requests": 3.0},
        )
        captured = []
        with patch("aictl.cmd.scale.discover_engines", return_value=[health]), \
             patch("aictl.cmd.scale.AutoScaler") as MockScaler, \
             patch("aictl.cmd.scale.print_json", side_effect=captured.append):
            MockScaler.return_value.evaluate.return_value = decision
            args = argparse.Namespace(engine="", json=True)
            ret = run_status(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0][0]["action"], "scale_up")
        self.assertEqual(captured[0][0]["desired_replicas"], 2)


# ── meter report ──────────────────────────────────────────────────────────────

class TestMeterReport(unittest.TestCase):
    """meter report shows cost attribution per entity."""

    def _make_parser(self):
        from aictl.cmd.meter import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_report_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["meter", "report"])
        self.assertEqual(args.func.__name__, "run_report")

    def test_sort_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["meter", "report", "--sort", "tokens"])
        self.assertEqual(args.sort, "tokens")

    def _make_bucket(self, entity_id, total_tokens, this_month=0, quota_month=0):
        from aictl.core.metering import TokenBucket
        return TokenBucket(
            entity_id=entity_id, entity_type="apikey",
            prompt_tokens=total_tokens // 2,
            completion_tokens=total_tokens // 2,
            total_tokens=total_tokens,
            request_count=10,
            first_request_at=time.time() - 86400,
            last_request_at=time.time(),
            tokens_today=total_tokens // 30,
            tokens_this_month=this_month,
            quota_tokens_per_month=quota_month,
        )

    def test_run_report_empty(self):
        from aictl.cmd.meter import run_report
        with patch("aictl.cmd.meter.TokenMeter") as MockMeter:
            MockMeter.return_value.list_usage.return_value = []
            args = argparse.Namespace(sort="cost", json=False)
            ret = run_report(args)
        self.assertEqual(ret, 0)

    def test_run_report_json(self):
        from aictl.cmd.meter import run_report
        buckets = [self._make_bucket("team-a", 1_000_000), self._make_bucket("team-b", 500_000)]
        captured = []
        with patch("aictl.cmd.meter.TokenMeter") as MockMeter, \
             patch("aictl.cmd.meter.print_json", side_effect=captured.append):
            MockMeter.return_value.list_usage.return_value = buckets
            MockMeter.return_value.estimate_cost.side_effect = lambda e: 0.1 if "a" in e else 0.05
            args = argparse.Namespace(sort="cost", json=True)
            ret = run_report(args)
        self.assertEqual(ret, 0)
        rows = captured[0]
        self.assertEqual(len(rows), 2)
        entities = {r["entity"] for r in rows}
        self.assertIn("team-a", entities)
        self.assertIn("team-b", entities)
        for r in rows:
            self.assertIn("cost_usd", r)
            self.assertIn("proj_month", r)

    def test_run_report_sorted_by_cost(self):
        from aictl.cmd.meter import run_report
        buckets = [self._make_bucket("team-b", 100_000), self._make_bucket("team-a", 1_000_000)]
        captured = []
        with patch("aictl.cmd.meter.TokenMeter") as MockMeter, \
             patch("aictl.cmd.meter.print_json", side_effect=captured.append):
            MockMeter.return_value.list_usage.return_value = buckets
            MockMeter.return_value.estimate_cost.side_effect = lambda e: 1.0 if e == "team-a" else 0.1
            args = argparse.Namespace(sort="cost", json=True)
            ret = run_report(args)
        self.assertEqual(ret, 0)
        # Cost-sorted: team-a first (higher cost)
        self.assertEqual(captured[0][0]["entity"], "team-a")

    def test_run_report_sorted_by_entity(self):
        from aictl.cmd.meter import run_report
        buckets = [self._make_bucket("team-c", 1_000), self._make_bucket("team-a", 2_000)]
        captured = []
        with patch("aictl.cmd.meter.TokenMeter") as MockMeter, \
             patch("aictl.cmd.meter.print_json", side_effect=captured.append):
            MockMeter.return_value.list_usage.return_value = buckets
            MockMeter.return_value.estimate_cost.return_value = 0.0
            args = argparse.Namespace(sort="entity", json=True)
            ret = run_report(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0][0]["entity"], "team-a")

    def test_run_report_quota_pct(self):
        from aictl.cmd.meter import run_report
        buckets = [self._make_bucket("team-a", 100_000, this_month=50_000, quota_month=100_000)]
        captured = []
        with patch("aictl.cmd.meter.TokenMeter") as MockMeter, \
             patch("aictl.cmd.meter.print_json", side_effect=captured.append):
            MockMeter.return_value.list_usage.return_value = buckets
            MockMeter.return_value.estimate_cost.return_value = 0.01
            args = argparse.Namespace(sort="cost", json=True)
            ret = run_report(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0][0]["quota"], "50%")


if __name__ == "__main__":
    unittest.main()
