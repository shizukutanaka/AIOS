"""Pass 76 regression tests: tenant lifecycle, bench slo/baseline, alert command."""

from __future__ import annotations

import argparse
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock


# ── tenant lifecycle ──────────────────────────────────────────────────────────

class TestTenantLifecycle(unittest.TestCase):
    """tenant create/delete/inspect/list — persistent tenant registry."""

    def _make_parser(self):
        from aictl.cmd.tenant import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_list_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["tenant", "list"])
        self.assertEqual(args.func.__name__, "run_list")

    def test_create_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["tenant", "create", "acme"])
        self.assertEqual(args.func.__name__, "run_create")
        self.assertEqual(args.tenant_id, "acme")

    def test_delete_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["tenant", "delete", "acme"])
        self.assertEqual(args.func.__name__, "run_delete")

    def test_inspect_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["tenant", "inspect", "acme"])
        self.assertEqual(args.func.__name__, "run_inspect")

    def test_create_class_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["tenant", "create", "acme", "--class", "regulated"])
        self.assertEqual(args.tenant_class, "regulated")

    def test_run_create_success(self):
        from aictl.cmd.tenant import run_create
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        captured = []
        with patch("aictl.cmd.tenant._registry_path",
                   return_value=tmpdir / "tenants.json"), \
             patch("aictl.cmd.tenant.print_json", side_effect=captured.append):
            args = argparse.Namespace(tenant_id="acme", name="Acme Corp",
                                      tenant_class="standard", state_dir=None, json=True)
            ret = run_create(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["id"], "acme")
        self.assertEqual(captured[0]["tenant_class"], "standard")

    def test_run_create_duplicate_fails(self):
        from aictl.cmd.tenant import run_create
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        reg_path = tmpdir / "tenants.json"
        with patch("aictl.cmd.tenant._registry_path", return_value=reg_path):
            args = argparse.Namespace(tenant_id="acme", name="Acme",
                                      tenant_class="standard", state_dir=None, json=False)
            run_create(args)
            ret = run_create(args)
        self.assertEqual(ret, 1)

    def test_run_delete_success(self):
        from aictl.cmd.tenant import run_create, run_delete
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        reg_path = tmpdir / "tenants.json"
        with patch("aictl.cmd.tenant._registry_path", return_value=reg_path):
            create_args = argparse.Namespace(tenant_id="todelete", name="",
                                              tenant_class="dev", state_dir=None, json=False)
            run_create(create_args)
            del_args = argparse.Namespace(tenant_id="todelete", state_dir=None, json=False)
            ret = run_delete(del_args)
        self.assertEqual(ret, 0)
        reg = json.loads(reg_path.read_text())
        self.assertNotIn("todelete", reg)

    def test_run_delete_not_found(self):
        from aictl.cmd.tenant import run_delete
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        with patch("aictl.cmd.tenant._registry_path", return_value=tmpdir / "t.json"):
            args = argparse.Namespace(tenant_id="ghost", state_dir=None, json=False)
            ret = run_delete(args)
        self.assertEqual(ret, 1)

    def test_run_inspect_found(self):
        from aictl.cmd.tenant import run_create, run_inspect
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        reg_path = tmpdir / "tenants.json"
        captured = []
        with patch("aictl.cmd.tenant._registry_path", return_value=reg_path):
            create_args = argparse.Namespace(tenant_id="inspme", name="Inspect Me",
                                              tenant_class="regulated", state_dir=None, json=False)
            run_create(create_args)
            with patch("aictl.cmd.tenant.print_json", side_effect=captured.append):
                insp_args = argparse.Namespace(tenant_id="inspme", state_dir=None, json=True)
                ret = run_inspect(insp_args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["id"], "inspme")
        self.assertIn("class_limits", captured[0])

    def test_run_inspect_not_found(self):
        from aictl.cmd.tenant import run_inspect
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        with patch("aictl.cmd.tenant._registry_path", return_value=tmpdir / "t.json"):
            args = argparse.Namespace(tenant_id="ghost", state_dir=None, json=False)
            ret = run_inspect(args)
        self.assertEqual(ret, 1)

    def test_run_list_empty(self):
        from aictl.cmd.tenant import run_list
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        with patch("aictl.cmd.tenant._registry_path", return_value=tmpdir / "t.json"):
            args = argparse.Namespace(state_dir=None, json=False)
            ret = run_list(args)
        self.assertEqual(ret, 0)

    def test_run_list_json(self):
        from aictl.cmd.tenant import run_create, run_list
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        reg_path = tmpdir / "tenants.json"
        captured = []
        with patch("aictl.cmd.tenant._registry_path", return_value=reg_path):
            for name in ("t1", "t2"):
                run_create(argparse.Namespace(tenant_id=name, name=name,
                                               tenant_class="dev", state_dir=None, json=False))
            with patch("aictl.cmd.tenant.print_json", side_effect=captured.append):
                ret = run_list(argparse.Namespace(state_dir=None, json=True))
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 2)


# ── bench slo / baseline ──────────────────────────────────────────────────────

class TestBenchSloBaseline(unittest.TestCase):
    """bench slo / bench baseline subcommands."""

    def _make_parser(self):
        from aictl.cmd.bench import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_slo_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["bench", "slo", "http://localhost:11434"])
        self.assertEqual(args.func.__name__, "run_slo")
        self.assertEqual(args.endpoint, "http://localhost:11434")

    def test_baseline_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["bench", "baseline"])
        self.assertEqual(args.func.__name__, "run_baseline")

    def test_run_baseline_json(self):
        from aictl.cmd.bench import run_baseline
        captured = []
        with patch("aictl.cmd.bench.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True)
            ret = run_baseline(args)
        self.assertEqual(ret, 0)
        self.assertIsInstance(captured[0], list)
        self.assertGreater(len(captured[0]), 0)
        self.assertIn("model", captured[0][0])
        self.assertIn("ttft_ms_p95", captured[0][0])

    def test_run_slo_pass(self):
        from aictl.cmd.bench import run_slo
        from aictl.runtime.benchmark import BenchResult
        good_result = BenchResult(
            endpoint="http://localhost:11434", model="llama3",
            requests=5, errors=0,
            ttft_ms_avg=100.0, ttft_ms_p95=200.0,
            tokens_per_sec=20.0, total_ms_avg=500.0,
            duration_sec=5.0, tokens_generated=100,
        )
        captured = []
        with patch("aictl.cmd.bench.run_benchmark", return_value=good_result), \
             patch("aictl.cmd.bench.print_json", side_effect=captured.append):
            args = argparse.Namespace(endpoint="http://localhost:11434", model="",
                                      requests=5, json=True)
            ret = run_slo(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["slo_passed"])

    def test_run_slo_fail_ttft(self):
        from aictl.cmd.bench import run_slo
        from aictl.runtime.benchmark import BenchResult
        bad_result = BenchResult(
            endpoint="http://localhost:11434", model="llama3",
            requests=5, errors=0,
            ttft_ms_avg=600.0, ttft_ms_p95=900.0,  # exceeds 500ms SLO
            tokens_per_sec=20.0, total_ms_avg=1200.0,
            duration_sec=5.0, tokens_generated=100,
        )
        captured = []
        with patch("aictl.cmd.bench.run_benchmark", return_value=bad_result), \
             patch("aictl.cmd.bench.print_json", side_effect=captured.append):
            args = argparse.Namespace(endpoint="http://localhost:11434", model="",
                                      requests=5, json=True)
            ret = run_slo(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["slo_passed"])

    def test_run_slo_benchmark_error(self):
        from aictl.cmd.bench import run_slo
        with patch("aictl.cmd.bench.run_benchmark", side_effect=ConnectionRefusedError):
            args = argparse.Namespace(endpoint="http://localhost:1", model="",
                                      requests=5, json=False)
            ret = run_slo(args)
        self.assertEqual(ret, 1)


# ── alert command ─────────────────────────────────────────────────────────────

class TestAlertCommand(unittest.TestCase):
    """aictl alert — SLO alert rules management and history."""

    def _make_parser(self):
        from aictl.cmd.alert import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_rules_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["alert", "rules"])
        self.assertEqual(args.func.__name__, "run_rules")

    def test_test_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["alert", "test", "AIOSEngineDown"])
        self.assertEqual(args.func.__name__, "run_test")
        self.assertEqual(args.rule, "AIOSEngineDown")

    def test_silence_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["alert", "silence", "--duration", "30m"])
        self.assertEqual(args.func.__name__, "run_silence")
        self.assertEqual(args.duration, "30m")

    def test_history_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["alert", "history"])
        self.assertEqual(args.func.__name__, "run_history")

    def test_run_rules_json(self):
        from aictl.cmd.alert import run_rules
        captured = []
        with patch("aictl.cmd.alert.print_json", side_effect=captured.append):
            args = argparse.Namespace(yaml=False, json=True)
            ret = run_rules(args)
        self.assertEqual(ret, 0)
        self.assertGreater(captured[0]["rule_count"], 0)
        rules = captured[0]["rules"]
        names = [r["alert"] for r in rules]
        self.assertIn("AIOSEngineDown", names)
        self.assertIn("AIOSHighErrorRate", names)

    def test_run_rules_all_six_alerts(self):
        from aictl.cmd.alert import run_rules, _RULE_NAMES
        captured = []
        with patch("aictl.cmd.alert.print_json", side_effect=captured.append):
            args = argparse.Namespace(yaml=False, json=True)
            run_rules(args)
        names = {r["alert"] for r in captured[0]["rules"]}
        for expected in _RULE_NAMES:
            self.assertIn(expected, names)

    def test_run_test_found(self):
        from aictl.cmd.alert import run_test
        captured = []
        with patch("aictl.cmd.alert.print_json", side_effect=captured.append):
            args = argparse.Namespace(rule="AIOSEngineDown", json=True)
            ret = run_test(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["found"])
        self.assertEqual(captured[0]["status"], "valid")

    def test_run_test_not_found(self):
        from aictl.cmd.alert import run_test
        captured = []
        with patch("aictl.cmd.alert.print_json", side_effect=captured.append):
            args = argparse.Namespace(rule="NonExistentRule", json=True)
            ret = run_test(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["found"])

    def test_run_silence_json(self):
        from aictl.cmd.alert import run_silence
        captured = []
        with patch("aictl.cmd.alert.print_json", side_effect=captured.append):
            args = argparse.Namespace(duration="2h", reason="maintenance", json=True)
            ret = run_silence(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["silenced"])
        self.assertEqual(captured[0]["duration"], "2h")
        self.assertGreater(captured[0]["expires_at"], time.time())

    def test_run_history_empty(self):
        from aictl.cmd.alert import run_history
        from aictl.core.events import get_bus
        args = argparse.Namespace(last=20, json=False)
        ret = run_history(args)
        self.assertEqual(ret, 0)

    def test_run_history_json(self):
        from aictl.cmd.alert import run_history, run_silence
        # Seed the bus with an alert event first
        run_silence(argparse.Namespace(duration="1m", reason="test-seed", json=False))
        captured = []
        with patch("aictl.cmd.alert.print_json", side_effect=captured.append):
            args = argparse.Namespace(last=50, json=True)
            ret = run_history(args)
        self.assertEqual(ret, 0)
        self.assertIsInstance(captured[0], list)

    def test_alert_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["alert", "rules"])
        self.assertEqual(args.func.__name__, "run_rules")


if __name__ == "__main__":
    unittest.main()
