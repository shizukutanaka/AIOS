"""Pass 69 regression tests: recipe validate, otel alerts (Prometheus rules)."""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch, MagicMock


class TestRecipeValidate(unittest.TestCase):
    """recipe validate checks recipe configuration for common errors."""

    def _make_parser(self):
        from aictl.cmd.recipe import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_validate_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["recipe", "validate"])
        self.assertEqual(args.func.__name__, "run_validate")

    def test_validate_optional_name(self):
        parser = self._make_parser()
        args = parser.parse_args(["recipe", "validate", "local-chat"])
        self.assertEqual(args.name, "local-chat")

    def test_validate_manifest_clean_recipe(self):
        from aictl.cmd.recipe import validate_manifest
        from aictl.stack.manifest import StackManifest, ServiceDef
        m = StackManifest(name="ok", services=[
            ServiceDef(name="chat", runtime="vllm", model="llama3", port=8000, replicas=1)
        ])
        problems = validate_manifest(m)
        self.assertEqual(problems, [])

    def test_validate_manifest_no_name(self):
        from aictl.cmd.recipe import validate_manifest
        from aictl.stack.manifest import StackManifest, ServiceDef
        m = StackManifest(name="", services=[ServiceDef(name="x", runtime="ollama")])
        problems = validate_manifest(m)
        self.assertTrue(any("no name" in p for p in problems))

    def test_validate_manifest_no_services(self):
        from aictl.cmd.recipe import validate_manifest
        from aictl.stack.manifest import StackManifest
        m = StackManifest(name="empty", services=[])
        problems = validate_manifest(m)
        self.assertTrue(any("no services" in p for p in problems))

    def test_validate_manifest_unknown_runtime(self):
        from aictl.cmd.recipe import validate_manifest
        from aictl.stack.manifest import StackManifest, ServiceDef
        m = StackManifest(name="r", services=[
            ServiceDef(name="x", runtime="bogus-engine")
        ])
        problems = validate_manifest(m)
        self.assertTrue(any("unknown runtime" in p for p in problems))

    def test_validate_manifest_duplicate_names(self):
        from aictl.cmd.recipe import validate_manifest
        from aictl.stack.manifest import StackManifest, ServiceDef
        m = StackManifest(name="r", services=[
            ServiceDef(name="dup", runtime="ollama"),
            ServiceDef(name="dup", runtime="ollama"),
        ])
        problems = validate_manifest(m)
        self.assertTrue(any("duplicate service name" in p for p in problems))

    def test_validate_manifest_duplicate_ports(self):
        from aictl.cmd.recipe import validate_manifest
        from aictl.stack.manifest import StackManifest, ServiceDef
        m = StackManifest(name="r", services=[
            ServiceDef(name="a", runtime="ollama", port=8000),
            ServiceDef(name="b", runtime="ollama", port=8000),
        ])
        problems = validate_manifest(m)
        self.assertTrue(any("already used" in p for p in problems))

    def test_validate_manifest_bad_replicas(self):
        from aictl.cmd.recipe import validate_manifest
        from aictl.stack.manifest import StackManifest, ServiceDef
        m = StackManifest(name="r", services=[
            ServiceDef(name="x", runtime="ollama", replicas=0)
        ])
        problems = validate_manifest(m)
        self.assertTrue(any("replicas" in p for p in problems))

    def test_validate_manifest_vllm_requires_model(self):
        from aictl.cmd.recipe import validate_manifest
        from aictl.stack.manifest import StackManifest, ServiceDef
        m = StackManifest(name="r", services=[
            ServiceDef(name="x", runtime="vllm", model="")
        ])
        problems = validate_manifest(m)
        self.assertTrue(any("requires a model" in p for p in problems))

    def test_validate_manifest_port_out_of_range(self):
        from aictl.cmd.recipe import validate_manifest
        from aictl.stack.manifest import StackManifest, ServiceDef
        m = StackManifest(name="r", services=[
            ServiceDef(name="x", runtime="ollama", port=99999)
        ])
        problems = validate_manifest(m)
        self.assertTrue(any("out of range" in p for p in problems))

    def test_run_validate_unknown_recipe_returns_1(self):
        from aictl.cmd.recipe import run_validate
        with patch("aictl.cmd.recipe.get_recipe", return_value=None):
            args = argparse.Namespace(name="nonexistent", json=True)
            captured = []
            with patch("aictl.cmd.recipe.print_json", side_effect=captured.append):
                ret = run_validate(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["valid"])

    def test_run_validate_all_recipes_json(self):
        from aictl.cmd.recipe import run_validate
        from aictl.stack.manifest import StackManifest, ServiceDef
        clean = StackManifest(name="ok", services=[
            ServiceDef(name="x", runtime="ollama")
        ])
        captured = []
        with patch("aictl.cmd.recipe.list_recipes", return_value=["ok"]), \
             patch("aictl.cmd.recipe.get_recipe", return_value=clean), \
             patch("aictl.cmd.recipe.print_json", side_effect=captured.append):
            args = argparse.Namespace(name="", json=True)
            ret = run_validate(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["all_valid"])


class TestOtelAlerts(unittest.TestCase):
    """otel alerts generates Prometheus alerting rules from SLO targets."""

    def _make_parser(self):
        from aictl.cmd.otel import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_alerts_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["otel", "alerts"])
        self.assertEqual(args.func.__name__, "run_alerts")

    def test_generate_alert_rules_returns_yaml(self):
        from aictl.metrics.alerts import generate_alert_rules
        rules = generate_alert_rules()
        self.assertIn("groups:", rules)
        self.assertIn("aios_slo_alerts", rules)
        self.assertIn("alert: AIOSEngineDown", rules)

    def test_generate_alert_rules_uses_target_thresholds(self):
        from aictl.metrics.alerts import generate_alert_rules
        from aictl.metrics.slo import SLOTarget
        target = SLOTarget(error_rate_max=0.10, queue_depth_max=50)
        rules = generate_alert_rules(target)
        self.assertIn("aios_inference_error_rate > 0.1", rules)
        self.assertIn("aios_inference_queue_depth > 50", rules)

    def test_generate_alert_rules_has_critical_and_warning(self):
        from aictl.metrics.alerts import generate_alert_rules
        rules = generate_alert_rules()
        self.assertIn("severity: critical", rules)
        self.assertIn("severity: warning", rules)

    def test_generate_alert_rules_references_real_metrics(self):
        from aictl.metrics.alerts import generate_alert_rules
        rules = generate_alert_rules()
        for metric in ("aios_engine_reachable", "aios_inference_error_rate",
                       "aios_inference_queue_depth", "aios_inference_kv_cache_utilization",
                       "aios_psi_memory_some_avg10"):
            self.assertIn(metric, rules)

    def test_run_alerts_writes_file(self):
        from aictl.cmd.otel import run_alerts
        import tempfile
        import pathlib
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        out = str(tmpdir / "alerts.yaml")
        args = argparse.Namespace(output=out, state_dir=None)
        ret = run_alerts(args)
        self.assertEqual(ret, 0)
        content = pathlib.Path(out).read_text()
        self.assertIn("aios_slo_alerts", content)

    def test_run_alerts_stdout(self):
        from aictl.cmd.otel import run_alerts
        printed = []
        with patch("builtins.print", side_effect=lambda *a, **k: printed.append(a)):
            args = argparse.Namespace(output="", state_dir=None)
            ret = run_alerts(args)
        self.assertEqual(ret, 0)
        self.assertTrue(printed)

    def test_alerts_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["otel", "alerts"])
        self.assertEqual(args.func.__name__, "run_alerts")


if __name__ == "__main__":
    unittest.main()
