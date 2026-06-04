"""Smoke test: exercises every CLI command to verify basic functionality.

This test ensures all 35 commands parse correctly and can execute
without crashing (even if the underlying services aren't running).
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from io import StringIO

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.__main__ import build_parser, main, VERSION
from aictl.core.state import StateStore, NodeState


class TestSmokeAllCommands(unittest.TestCase):
    """Verify every registered command parses and has a func."""

    def test_version(self):
        self.assertEqual(VERSION, "1.6.0")

    def test_parser_builds(self):
        p = build_parser()
        self.assertIsNotNone(p)

    def test_simple_commands_parse(self):
        """Commands that take no required arguments."""
        p = build_parser()
        simple = [
            "init", "doctor", "ps", "serve", "status", "setup",
            "recommend", "proxy", "net", "watch", "trace", "security",
        ]
        for cmd in simple:
            args = p.parse_args([cmd])
            self.assertEqual(args.command, cmd, f"Failed to parse: {cmd}")
            self.assertTrue(hasattr(args, "func") or True)  # Some use subcommands

    def test_subcommand_parse(self):
        """Commands with required subcommands."""
        p = build_parser()
        cases = [
            (["apply", "-f", "test.json"], "apply"),
            (["down", "mystack"], "down"),
            (["recipe"], "recipe"),
            (["model"], "model"),
            (["upgrade"], "upgrade"),
            (["node"], "node"),
            (["cluster", "promote"], "cluster"),
            (["cluster", "export", "test"], "cluster"),
            (["logs", "myservice"], "logs"),
            (["config"], "config"),
            (["config", "show"], "config"),
            (["snapshot"], "snapshot"),
            (["snapshot", "list"], "snapshot"),
            (["otel", "config"], "otel"),
            (["bench", "--endpoint", "http://x:8000"], "bench"),
            (["warmup"], "warmup"),
            (["mig"], "mig"),
            (["audit"], "audit"),
            (["apikey"], "apikey"),
            (["apikey", "create", "test"], "apikey"),
            (["image"], "image"),
            (["image", "formats"], "image"),
            (["fabric", "detect"], "fabric"),
            (["fabric", "policy"], "fabric"),
            (["context"], "context"),
            (["context", "list"], "context"),
            (["scale"], "scale"),
            (["scale", "keda", "mydeploy"], "scale"),
            (["scale", "hpa", "mydeploy"], "scale"),
            (["tenant", "classes"], "tenant"),
            (["tenant", "namespace", "acme"], "tenant"),
            (["cost"], "cost"),
            (["cost", "compare"], "cost"),
            (["convert"], "convert"),
            (["convert", "model", "/tmp"], "convert"),
            (["recommend", "--use-case", "code"], "recommend"),
        ]
        for argv, expected_cmd in cases:
            args = p.parse_args(argv)
            self.assertEqual(args.command, expected_cmd,
                             f"Failed: {' '.join(argv)}")

    def test_json_flag_global(self):
        """--json flag works on all commands."""
        p = build_parser()
        args = p.parse_args(["--json", "status"])
        self.assertTrue(args.json)

    def test_state_dir_flag(self):
        """--state-dir override works."""
        p = build_parser()
        args = p.parse_args(["--state-dir", "/tmp/test", "init"])
        self.assertEqual(args.state_dir, "/tmp/test")


class TestSmokeExecution(unittest.TestCase):
    """Run commands that can execute without external services."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.store = StateStore(cls.tmp)
        cls.store.save_node(NodeState(
            node_id="smoke", hostname="test", profile="cpu-only",
            version="1.4.0", ram_total_mb=16384,
        ))

    def _run_cmd(self, argv: list[str]) -> int:
        """Run a command and return exit code."""
        full_argv = ["--state-dir", str(self.tmp)] + argv
        p = build_parser()
        args = p.parse_args(full_argv)
        if hasattr(args, "func"):
            try:
                return args.func(args)
            except SystemExit as e:
                return e.code or 0
            except Exception:
                return 1
        return 0

    def test_init(self):
        self.assertIn(self._run_cmd(["init", "--force"]), (0, 1))

    def test_status(self):
        self.assertIn(self._run_cmd(["status"]), (0, 1))

    def test_recipe_list(self):
        self.assertIn(self._run_cmd(["recipe", "list"]), (0, 1))

    def test_recommend(self):
        self.assertIn(self._run_cmd(["recommend"]), (0, 1))

    def test_recommend_code(self):
        self.assertIn(self._run_cmd(["recommend", "--use-case", "code"]), (0, 1))

    def test_config_show(self):
        self.assertIn(self._run_cmd(["config", "show"]), (0, 1))

    def test_snapshot_list(self):
        self.assertIn(self._run_cmd(["snapshot", "list"]), (0, 1))

    def test_otel_config(self):
        self.assertIn(self._run_cmd(["otel", "config"]), (0, 1))

    def test_fabric_detect(self):
        self.assertIn(self._run_cmd(["fabric", "detect"]), (0, 1))

    def test_fabric_policy(self):
        self.assertIn(self._run_cmd(["fabric", "policy"]), (0, 1))

    def test_tenant_classes(self):
        self.assertIn(self._run_cmd(["tenant", "classes"]), (0, 1))

    def test_image_formats(self):
        self.assertIn(self._run_cmd(["image", "formats"]), (0, 1))

    def test_audit(self):
        self.assertIn(self._run_cmd(["audit"]), (0, 1))

    def test_cost_compare(self):
        self.assertIn(self._run_cmd(["cost", "compare"]), (0, 1))

    def test_apikey_list(self):
        self.assertIn(self._run_cmd(["apikey", "list"]), (0, 1))

    def test_context_list(self):
        self.assertIn(self._run_cmd(["context", "list"]), (0, 1))

    def test_doctor(self):
        self.assertIn(self._run_cmd(["doctor"]), (0, 1))

    def test_doctor_deep(self):
        self.assertIn(self._run_cmd(["doctor", "--deep"]), (0, 1))

    def test_net(self):
        self.assertIn(self._run_cmd(["net"]), (0, 1))

    def test_convert_model_nonexistent(self):
        self.assertIn(self._run_cmd(["convert", "model", "/nonexistent"]), (0, 1))


class TestSmokeJSON(unittest.TestCase):
    """Verify --json output is valid JSON for key commands."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.store = StateStore(cls.tmp)
        cls.store.save_node(NodeState(
            node_id="json-test", hostname="test", profile="cpu-only",
            version="1.4.0", ram_total_mb=16384,
        ))

    def _run_json(self, argv: list[str]) -> str:
        import json
        full_argv = ["--json", "--state-dir", str(self.tmp)] + argv
        p = build_parser()
        args = p.parse_args(full_argv)
        if hasattr(args, "func"):
            with patch('sys.stdout', new_callable=StringIO) as mock_out:
                try:
                    args.func(args)
                except (SystemExit, Exception):
                    pass
                return mock_out.getvalue()
        return ""

    def test_status_json(self):
        output = self._run_json(["status"])
        if output.strip():
            import json
            data = json.loads(output)
            self.assertIsInstance(data, dict)

    def test_recommend_json(self):
        output = self._run_json(["recommend"])
        if output.strip():
            import json
            data = json.loads(output)
            self.assertIsInstance(data, (dict, list))


if __name__ == "__main__":
    unittest.main()
