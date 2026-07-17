"""Pass 66 regression tests: aictl export, aictl import."""

from __future__ import annotations

import argparse
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock


class TestExportCommand(unittest.TestCase):
    """export stack and export bundle subcommands."""

    def _make_parser(self):
        from aictl.cmd.export import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_stack_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["export", "stack", "mystack"])
        self.assertEqual(args.func.__name__, "run_stack")
        self.assertEqual(args.name, "mystack")

    def test_bundle_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["export", "bundle"])
        self.assertEqual(args.func.__name__, "run_bundle")

    def test_stack_output_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["export", "stack", "s", "-o", "/tmp/out.yaml"])
        self.assertEqual(args.output, "/tmp/out.yaml")

    def test_export_stack_returns_1_when_not_found(self):
        from aictl.cmd.export import run_stack
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        with patch("aictl.cmd.export.StateStore") as mock_cls:
            mock_store = MagicMock()
            mock_store.load_stacks.return_value = []
            mock_cls.return_value = mock_store
            args = argparse.Namespace(name="gone", output="", json=False, state_dir=tmpdir)
            ret = run_stack(args)
        self.assertEqual(ret, 1)

    def test_export_stack_json_stdout(self):
        from aictl.cmd.export import run_stack
        from aictl.core.state import StackEntry
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        entry = StackEntry(name="mystack", file="/path/stack.yaml",
                           applied_at=1000.0, status="running")

        printed = []
        with patch("aictl.cmd.export.StateStore") as mock_cls, \
             patch("builtins.print", side_effect=lambda x: printed.append(x)):
            mock_store = MagicMock()
            mock_store.load_stacks.return_value = [entry]
            mock_cls.return_value = mock_store
            args = argparse.Namespace(name="mystack", output="", json=True, state_dir=tmpdir)
            ret = run_stack(args)
        self.assertEqual(ret, 0)
        self.assertTrue(any("mystack" in str(p) for p in printed))

    def test_export_stack_yaml_stdout(self):
        from aictl.cmd.export import run_stack
        from aictl.core.state import StackEntry
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        entry = StackEntry(name="mystack", file="/path/stack.yaml",
                           applied_at=1000.0, status="running")

        printed = []
        with patch("aictl.cmd.export.StateStore") as mock_cls, \
             patch("builtins.print", side_effect=lambda x: printed.append(x)):
            mock_store = MagicMock()
            mock_store.load_stacks.return_value = [entry]
            mock_cls.return_value = mock_store
            args = argparse.Namespace(name="mystack", output="", json=False, state_dir=tmpdir)
            ret = run_stack(args)
        self.assertEqual(ret, 0)
        combined = "\n".join(str(p) for p in printed)
        self.assertIn("name: mystack", combined)

    def test_export_stack_writes_file(self):
        from aictl.cmd.export import run_stack
        from aictl.core.state import StackEntry
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        out_path = str(tmpdir / "out.yaml")
        entry = StackEntry(name="s", file="f.yaml", applied_at=0.0, status="running")

        with patch("aictl.cmd.export.StateStore") as mock_cls:
            mock_store = MagicMock()
            mock_store.load_stacks.return_value = [entry]
            mock_cls.return_value = mock_store
            args = argparse.Namespace(name="s", output=out_path, json=False, state_dir=tmpdir)
            ret = run_stack(args)
        self.assertEqual(ret, 0)
        self.assertTrue(pathlib.Path(out_path).exists())

    def test_export_bundle_json_stdout(self):
        from aictl.cmd.export import run_bundle
        from aictl.core.state import NodeState
        tmpdir = pathlib.Path(tempfile.mkdtemp())

        printed = []
        with patch("aictl.cmd.export.StateStore") as mock_cls, \
             patch("builtins.print", side_effect=lambda x: printed.append(x)):
            mock_store = MagicMock()
            mock_store.load_stacks.return_value = []
            mock_store.list_models.return_value = []
            mock_store.load_node.return_value = NodeState(
                node_id="n1", hostname="host", initialized_at=0.0,
                profile="", version="1.6.0", mode="local",
                gpu_count=0, vram_total_mb=0, ram_total_mb=0
            )
            mock_cls.return_value = mock_store
            args = argparse.Namespace(output="", pretty=True, state_dir=tmpdir)
            ret = run_bundle(args)
        self.assertEqual(ret, 0)
        combined = "\n".join(str(p) for p in printed)
        bundle = json.loads(combined)
        self.assertIn("export_version", bundle)
        self.assertIn("stacks", bundle)
        self.assertIn("models", bundle)
        self.assertIn("exported_at", bundle)

    def test_export_bundle_writes_file(self):
        from aictl.cmd.export import run_bundle
        from aictl.core.state import NodeState
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        out_path = str(tmpdir / "bundle.json")

        with patch("aictl.cmd.export.StateStore") as mock_cls:
            mock_store = MagicMock()
            mock_store.load_stacks.return_value = []
            mock_store.list_models.return_value = []
            mock_store.load_node.return_value = NodeState(
                node_id="n1", hostname="h", initialized_at=0.0,
                profile="", version="1.6.0", mode="local",
                gpu_count=0, vram_total_mb=0, ram_total_mb=0
            )
            mock_cls.return_value = mock_store
            args = argparse.Namespace(output=out_path, pretty=True, state_dir=tmpdir)
            ret = run_bundle(args)
        self.assertEqual(ret, 0)
        data = json.loads(pathlib.Path(out_path).read_text())
        self.assertEqual(data["export_version"], "1")

    def test_export_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["export", "bundle"])
        self.assertEqual(args.func.__name__, "run_bundle")


class TestImportCommand(unittest.TestCase):
    """import — restore stacks and models from a bundle file."""

    def _make_parser(self):
        from aictl.cmd.import_cmd import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_import_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["import", "bundle.json"])
        self.assertEqual(args.func.__name__, "run")
        self.assertEqual(args.file, "bundle.json")

    def test_dry_run_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["import", "bundle.json", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_skip_models_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["import", "bundle.json", "--skip-models"])
        self.assertTrue(args.skip_models)

    def test_skip_stacks_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["import", "bundle.json", "--skip-stacks"])
        self.assertTrue(args.skip_stacks)

    def test_returns_1_when_file_not_found(self):
        from aictl.cmd.import_cmd import run
        args = argparse.Namespace(
            file="/nonexistent/bundle.json", dry_run=False,
            skip_models=False, skip_stacks=False, json=True, state_dir=None
        )
        captured = []
        with patch("aictl.cmd.import_cmd.print_json", side_effect=captured.append):
            ret = run(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["success"])

    def test_imports_stacks_from_bundle(self):
        from aictl.cmd.import_cmd import run
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        bundle = {
            "export_version": "1",
            "exported_at": "2026-01-01T00:00:00Z",
            "node": {},
            "stacks": [
                {"name": "s1", "file": "s1.yaml", "applied_at": 1000.0,
                 "status": "running", "services": []}
            ],
            "models": [],
        }
        bundle_path = tmpdir / "bundle.json"
        bundle_path.write_text(json.dumps(bundle))

        captured = []
        with patch("aictl.cmd.import_cmd.StateStore") as mock_cls, \
             patch("aictl.cmd.import_cmd.print_json", side_effect=captured.append):
            mock_store = MagicMock()
            mock_cls.return_value = mock_store
            args = argparse.Namespace(
                file=str(bundle_path), dry_run=False,
                skip_models=False, skip_stacks=False, json=True, state_dir=tmpdir
            )
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["success"])
        self.assertEqual(captured[0]["stacks_imported"], 1)
        mock_store.upsert_stack.assert_called_once()

    def test_dry_run_does_not_write_state(self):
        from aictl.cmd.import_cmd import run
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        bundle = {
            "export_version": "1",
            "exported_at": "2026-01-01T00:00:00Z",
            "node": {},
            "stacks": [{"name": "s", "file": "s.yaml", "applied_at": 0.0,
                        "status": "running", "services": []}],
            "models": [],
        }
        bundle_path = tmpdir / "bundle.json"
        bundle_path.write_text(json.dumps(bundle))

        captured = []
        with patch("aictl.cmd.import_cmd.StateStore") as mock_cls, \
             patch("aictl.cmd.import_cmd.print_json", side_effect=captured.append):
            mock_store = MagicMock()
            mock_cls.return_value = mock_store
            args = argparse.Namespace(
                file=str(bundle_path), dry_run=True,
                skip_models=False, skip_stacks=False, json=True, state_dir=tmpdir
            )
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["dry_run"])
        mock_store.upsert_stack.assert_not_called()

    def test_import_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["import", "x.json"])
        self.assertEqual(args.func.__name__, "run")


if __name__ == "__main__":
    unittest.main()
