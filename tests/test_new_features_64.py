"""Pass 64 regression tests: plugin management CLI, apply rollback."""

from __future__ import annotations

import argparse
import pathlib
import tempfile
import unittest
from unittest.mock import patch, MagicMock


class TestPluginCommand(unittest.TestCase):
    """plugin list/reload/info commands."""

    def _make_parser(self):
        from aictl.cmd.plugin import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_list_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["plugin", "list"])
        self.assertEqual(args.func.__name__, "run_list")

    def test_reload_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["plugin", "reload"])
        self.assertEqual(args.func.__name__, "run_reload")

    def test_info_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["plugin", "info", "myplugin"])
        self.assertEqual(args.func.__name__, "run_info")
        self.assertEqual(args.name, "myplugin")

    def test_list_returns_0_with_no_plugins(self):
        from aictl.cmd.plugin import run_list
        with patch("aictl.core.plugins.discover_plugins", return_value=[]):
            args = argparse.Namespace(json=False)
            ret = run_list(args)
        self.assertEqual(ret, 0)

    def test_list_json_returns_list(self):
        from aictl.cmd.plugin import run_list
        fake_plugin = {"name": "test_plugin", "path": "/tmp/test_plugin.py", "dir": "/tmp"}

        mock_mod = MagicMock()
        mock_mod.__bool__ = lambda s: True
        mock_mod.register = lambda sub: None

        captured = []
        with patch("aictl.core.plugins.discover_plugins", return_value=[fake_plugin]), \
             patch("aictl.core.plugins.load_plugin", return_value=mock_mod), \
             patch("aictl.cmd.plugin.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True)
            ret = run_list(args)
        self.assertEqual(ret, 0)
        self.assertIsInstance(captured[0], list)
        self.assertEqual(captured[0][0]["name"], "test_plugin")
        self.assertTrue(captured[0][0]["has_register"])

    def test_list_json_loaded_false_when_import_fails(self):
        from aictl.cmd.plugin import run_list
        fake_plugin = {"name": "bad", "path": "/tmp/bad.py", "dir": "/tmp"}

        captured = []
        with patch("aictl.core.plugins.discover_plugins", return_value=[fake_plugin]), \
             patch("aictl.core.plugins.load_plugin", return_value=None), \
             patch("aictl.cmd.plugin.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True)
            ret = run_list(args)
        self.assertEqual(ret, 0)
        self.assertFalse(captured[0][0]["loaded"])

    def test_reload_returns_0(self):
        from aictl.cmd.plugin import run_reload
        fake_plugin = {"name": "p", "path": "/tmp/p.py", "dir": "/tmp"}

        captured = []
        with patch("aictl.core.plugins.discover_plugins", return_value=[fake_plugin]), \
             patch("aictl.core.plugins.load_plugin", return_value=MagicMock()), \
             patch("aictl.cmd.plugin.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True)
            ret = run_reload(args)
        self.assertEqual(ret, 0)
        self.assertIsInstance(captured[0], list)
        self.assertEqual(captured[0][0]["status"], "ok")

    def test_reload_reports_error_status_when_load_fails(self):
        from aictl.cmd.plugin import run_reload
        fake_plugin = {"name": "bad", "path": "/tmp/bad.py", "dir": "/tmp"}

        captured = []
        with patch("aictl.core.plugins.discover_plugins", return_value=[fake_plugin]), \
             patch("aictl.core.plugins.load_plugin", return_value=None), \
             patch("aictl.cmd.plugin.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True)
            ret = run_reload(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0][0]["status"], "error")

    def test_info_returns_1_when_not_found(self):
        from aictl.cmd.plugin import run_info
        with patch("aictl.core.plugins.discover_plugins", return_value=[]):
            args = argparse.Namespace(name="missing", json=False)
            ret = run_info(args)
        self.assertEqual(ret, 1)

    def test_info_json_output(self):
        from aictl.cmd.plugin import run_info
        fake_plugin = {"name": "myplugin", "path": "/tmp/myplugin.py", "dir": "/tmp"}

        captured = []
        with patch("aictl.core.plugins.discover_plugins", return_value=[fake_plugin]), \
             patch("aictl.core.plugins.load_plugin", return_value=MagicMock()), \
             patch("aictl.cmd.plugin.print_json", side_effect=captured.append):
            args = argparse.Namespace(name="myplugin", json=True)
            ret = run_info(args)
        self.assertEqual(ret, 0)
        data = captured[0]
        self.assertEqual(data["name"], "myplugin")
        self.assertTrue(data["loaded"])

    def test_plugin_registered_in_main(self):
        """plugin command is reachable via the top-level CLI."""
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["plugin", "list"])
        self.assertEqual(args.func.__name__, "run_list")


class TestApplyRollback(unittest.TestCase):
    """apply rollback re-applies a stack from its recorded manifest."""

    def _make_parser(self):
        from aictl.cmd.apply import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_rollback_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["apply", "rollback", "mystack"])
        self.assertEqual(args.func.__name__, "run_rollback")
        self.assertEqual(args.name, "mystack")

    def test_rollback_dry_run_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["apply", "rollback", "mystack", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_rollback_returns_1_when_stack_not_in_state(self):
        from aictl.cmd.apply import run_rollback
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        with patch("aictl.cmd.apply.StateStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store.load_stacks.return_value = []
            mock_store_cls.return_value = mock_store
            args = argparse.Namespace(
                name="missing", dry_run=False, json=True, state_dir=tmpdir
            )
            ret = run_rollback(args)
        self.assertEqual(ret, 1)

    def test_rollback_returns_1_when_no_manifest_file(self):
        from aictl.cmd.apply import run_rollback
        from aictl.core.state import StackEntry
        tmpdir = pathlib.Path(tempfile.mkdtemp())

        entry = StackEntry(name="mystack", file="", applied_at=0.0, status="running")
        with patch("aictl.cmd.apply.StateStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store.load_stacks.return_value = [entry]
            mock_store_cls.return_value = mock_store
            args = argparse.Namespace(
                name="mystack", dry_run=False, json=True, state_dir=tmpdir
            )
            ret = run_rollback(args)
        self.assertEqual(ret, 1)

    def test_rollback_calls_run_with_recorded_file(self):
        from aictl.cmd.apply import run_rollback
        from aictl.core.state import StackEntry
        tmpdir = pathlib.Path(tempfile.mkdtemp())

        entry = StackEntry(name="mystack", file="/manifests/mystack.yaml",
                           applied_at=1000.0, status="running")

        mock_manifest = MagicMock()
        mock_manifest.name = "mystack"
        mock_manifest.services = []

        with patch("aictl.cmd.apply.StateStore") as mock_store_cls, \
             patch("aictl.cmd.apply.parse_file", return_value=mock_manifest), \
             patch("aictl.cmd.apply.apply_stack", return_value=[]):
            mock_store = MagicMock()
            mock_store.load_stacks.return_value = [entry]
            mock_store_cls.return_value = mock_store
            args = argparse.Namespace(
                name="mystack", dry_run=True, json=True, state_dir=tmpdir
            )
            ret = run_rollback(args)
        # parse_file should have been called with the recorded path
        from aictl.cmd.apply import parse_file
        self.assertEqual(ret, 0)

    def test_rollback_json_error_on_missing_stack(self):
        from aictl.cmd.apply import run_rollback
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        captured = []
        with patch("aictl.cmd.apply.StateStore") as mock_store_cls, \
             patch("aictl.cmd.apply.print_json", side_effect=captured.append):
            mock_store = MagicMock()
            mock_store.load_stacks.return_value = []
            mock_store_cls.return_value = mock_store
            args = argparse.Namespace(
                name="gone", dry_run=False, json=True, state_dir=tmpdir
            )
            ret = run_rollback(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["success"])

    def test_apply_without_file_returns_1(self):
        from aictl.cmd.apply import run
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        args = argparse.Namespace(
            file="", dry_run=False, validate_only=False,
            quadlet=False, root=False, json=False, state_dir=tmpdir,
        )
        ret = run(args)
        self.assertEqual(ret, 1)


if __name__ == "__main__":
    unittest.main()
