"""Pass 67 regression tests: config get, doctor --fix."""

from __future__ import annotations

import argparse
import pathlib
import tempfile
import unittest
from unittest.mock import patch, MagicMock


class TestConfigGet(unittest.TestCase):
    """config get retrieves a single value by dot-key."""

    def _make_parser(self):
        from aictl.cmd.config import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_get_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["config", "get", "engines.vllm"])
        self.assertEqual(args.func.__name__, "run_get")
        self.assertEqual(args.key, "engines.vllm")

    def test_get_json_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["config", "get", "engines.vllm", "--json"])
        self.assertTrue(args.json)

    def test_get_returns_1_on_unknown_key(self):
        from aictl.cmd.config import run_get
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        captured = []
        with patch("aictl.cmd.config.print_json", side_effect=captured.append):
            args = argparse.Namespace(key="does.not.exist", json=True, state_dir=str(tmpdir))
            ret = run_get(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["found"])

    def test_get_returns_value_for_valid_key(self):
        from aictl.cmd.config import run_get
        from aictl.core.config import load_config
        tmpdir = pathlib.Path(tempfile.mkdtemp())

        cfg = load_config(tmpdir)
        from dataclasses import asdict
        d = asdict(cfg)
        # pick a real top-level key
        top_key = next(iter(d.keys()))

        captured = []
        with patch("aictl.cmd.config.print_json", side_effect=captured.append):
            args = argparse.Namespace(key=top_key, json=True, state_dir=str(tmpdir))
            ret = run_get(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["found"])
        self.assertEqual(captured[0]["key"], top_key)

    def test_get_nested_engine_value(self):
        from aictl.cmd.config import run_get
        from aictl.core.config import load_config
        tmpdir = pathlib.Path(tempfile.mkdtemp())

        cfg = load_config(tmpdir)
        engines = cfg.engines.to_dict()
        if not engines:
            self.skipTest("no engines configured")
        engine_name = next(iter(engines.keys()))

        captured = []
        with patch("aictl.cmd.config.print_json", side_effect=captured.append):
            args = argparse.Namespace(key=f"engines.{engine_name}", json=True,
                                      state_dir=str(tmpdir))
            ret = run_get(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["found"])


class TestDoctorFix(unittest.TestCase):
    """doctor --fix builds remediation plans and auto-applies safe fixes."""

    def _make_parser(self):
        from aictl.cmd.doctor import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_fix_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["doctor", "--fix"])
        self.assertTrue(args.fix)

    def test_build_remediations_flags_uninitialized_node(self):
        from aictl.cmd.doctor import build_remediations
        mock_store = MagicMock()
        mock_store.is_initialized.return_value = False
        report = MagicMock()
        report.container_runtime = "podman"
        report.system.cgroup_v2 = True
        report.system.psi_enabled = True
        report.issues = []

        fixes = build_remediations(report, mock_store)
        init_fix = next((f for f in fixes if "init" in f["command"]), None)
        self.assertIsNotNone(init_fix)
        self.assertTrue(init_fix["auto"])

    def test_build_remediations_flags_missing_runtime(self):
        from aictl.cmd.doctor import build_remediations
        mock_store = MagicMock()
        mock_store.is_initialized.return_value = True
        report = MagicMock()
        report.container_runtime = "none"
        report.system.cgroup_v2 = True
        report.system.psi_enabled = True
        report.issues = []

        fixes = build_remediations(report, mock_store)
        rt_fix = next((f for f in fixes if "container runtime" in f["issue"].lower()), None)
        self.assertIsNotNone(rt_fix)
        self.assertFalse(rt_fix["auto"])  # requires sudo, not auto-applied

    def test_build_remediations_empty_when_healthy(self):
        from aictl.cmd.doctor import build_remediations
        mock_store = MagicMock()
        mock_store.is_initialized.return_value = True
        report = MagicMock()
        report.container_runtime = "podman"
        report.system.cgroup_v2 = True
        report.system.psi_enabled = True
        report.issues = []

        fixes = build_remediations(report, mock_store)
        self.assertEqual(fixes, [])

    def test_run_fix_json_output(self):
        from aictl.cmd.doctor import run_fix
        mock_store = MagicMock()
        mock_store.is_initialized.return_value = True
        report = MagicMock()
        report.container_runtime = "podman"
        report.system.cgroup_v2 = True
        report.system.psi_enabled = True
        report.issues = []

        captured = []
        with patch("aictl.cmd.doctor.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True, fix=True)
            ret = run_fix(args, mock_store, report)
        self.assertEqual(ret, 0)
        self.assertIn("remediations", captured[0])
        self.assertIn("applied", captured[0])

    def test_run_fix_auto_initializes_node(self):
        from aictl.cmd.doctor import run_fix
        mock_store = MagicMock()
        # First call (build_remediations) returns False, _auto_init checks again
        mock_store.is_initialized.return_value = False

        report = MagicMock()
        report.container_runtime = "podman"
        report.system.cgroup_v2 = True
        report.system.psi_enabled = True
        report.issues = []
        report.gpus = []
        report.system.ram_total_mb = 16000

        captured = []
        with patch("aictl.cmd.doctor.print_json", side_effect=captured.append), \
             patch("aictl.cmd.doctor.full_detect", return_value=report):
            args = argparse.Namespace(json=True, fix=True)
            ret = run_fix(args, mock_store, report)
        self.assertEqual(ret, 0)
        # Node init should have been applied
        self.assertIn("Node not initialized", captured[0]["applied"])
        mock_store.save_node.assert_called_once()

    def test_doctor_run_delegates_to_fix(self):
        from aictl.cmd.doctor import run
        tmpdir = pathlib.Path(tempfile.mkdtemp())

        report = MagicMock()
        report.container_runtime = "podman"
        report.system.cgroup_v2 = True
        report.system.psi_enabled = True
        report.issues = []

        captured = []
        with patch("aictl.cmd.doctor.full_detect", return_value=report), \
             patch("aictl.cmd.doctor.print_json", side_effect=captured.append), \
             patch("aictl.cmd.doctor.StateStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store.is_initialized.return_value = True
            mock_store_cls.return_value = mock_store
            args = argparse.Namespace(fix=True, deep=False, json=True, state_dir=str(tmpdir))
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertIn("remediations", captured[0])


if __name__ == "__main__":
    unittest.main()
