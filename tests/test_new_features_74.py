"""Pass 74 regression tests: hooks CLI, snapshot export/import."""

from __future__ import annotations

import argparse
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock


# ── hooks ─────────────────────────────────────────────────────────────────────

class TestHooksCommand(unittest.TestCase):
    """aictl hooks — inspect and test integration hooks."""

    def _make_parser(self):
        from aictl.cmd.hooks import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_list_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["hooks", "list"])
        self.assertEqual(args.func.__name__, "run_list")

    def test_test_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["hooks", "test", "on_stack_applied"])
        self.assertEqual(args.func.__name__, "run_test")
        self.assertEqual(args.name, "on_stack_applied")

    def test_emit_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["hooks", "emit", "stack.applied"])
        self.assertEqual(args.func.__name__, "run_emit")
        self.assertEqual(args.event_type, "stack.applied")

    def test_run_list_json(self):
        from aictl.cmd.hooks import run_list
        captured = []
        with patch("aictl.cmd.hooks.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True)
            ret = run_list(args)
        self.assertEqual(ret, 0)
        hooks = captured[0]
        self.assertIsInstance(hooks, list)
        self.assertGreater(len(hooks), 0)
        names = {h["name"] for h in hooks}
        self.assertIn("on_stack_applied", names)
        self.assertIn("on_slo_violation", names)

    def test_run_list_all_hooks_present(self):
        from aictl.cmd.hooks import run_list, _HOOKS
        captured = []
        with patch("aictl.cmd.hooks.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True)
            run_list(args)
        self.assertEqual(len(captured[0]), len(_HOOKS))

    def test_run_test_on_stack_applied(self):
        from aictl.cmd.hooks import run_test
        captured = []
        with patch("aictl.cmd.hooks.print_json", side_effect=captured.append):
            args = argparse.Namespace(name="on_stack_applied", json=True)
            ret = run_test(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["hook"], "on_stack_applied")
        self.assertEqual(captured[0]["status"], "ok")
        self.assertGreater(captured[0]["events_emitted"], 0)

    def test_run_test_on_slo_violation(self):
        from aictl.cmd.hooks import run_test
        captured = []
        with patch("aictl.cmd.hooks.print_json", side_effect=captured.append):
            args = argparse.Namespace(name="on_slo_violation", json=True)
            ret = run_test(args)
        self.assertEqual(ret, 0)
        self.assertGreater(captured[0]["events_emitted"], 0)

    def test_run_test_unknown_hook(self):
        from aictl.cmd.hooks import run_test
        args = argparse.Namespace(name="on_nonexistent", json=False)
        ret = run_test(args)
        self.assertEqual(ret, 1)

    def test_run_emit_adds_event(self):
        from aictl.cmd.hooks import run_emit
        from aictl.core.events import get_bus
        bus = get_bus()
        before = len(bus.recent(n=500))
        args = argparse.Namespace(event_type="test.custom.event", source="unit-test")
        ret = run_emit(args)
        self.assertEqual(ret, 0)
        after = len(bus.recent(n=500))
        self.assertGreater(after, before)

    def test_hooks_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["hooks", "list"])
        self.assertEqual(args.func.__name__, "run_list")


# ── snapshot export/import ────────────────────────────────────────────────────

class TestSnapshotExportImport(unittest.TestCase):
    """snapshot export/import — portable state bundles."""

    def _make_parser(self):
        from aictl.cmd.snapshot import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_export_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["snapshot", "export", "snap_001"])
        self.assertEqual(args.func.__name__, "run_export")
        self.assertEqual(args.id, "snap_001")

    def test_import_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["snapshot", "import", "snap.json"])
        self.assertEqual(args.func.__name__, "run_import")
        self.assertEqual(args.file, "snap.json")

    def test_import_restore_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["snapshot", "import", "snap.json", "--restore"])
        self.assertTrue(args.restore)

    def _make_snap_data(self, snap_id="snap_001_abc123"):
        return {
            "snapshot_id": snap_id,
            "created_at": time.time(),
            "version": "1.6.0",
            "node_state": {},
            "stacks": [{"name": "local-chat"}],
            "models": [{"name": "llama3"}],
            "cluster": {},
            "config": {},
        }

    def test_run_export_success(self):
        from aictl.cmd.snapshot import run_export
        from pathlib import Path
        snap_data = self._make_snap_data()
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        snap_file = tmpdir / f"{snap_data['snapshot_id']}.json"
        snap_file.write_text(json.dumps(snap_data))
        out_file = str(tmpdir / "exported.json")
        captured = []
        with patch("aictl.cmd.snapshot.StateStore") as MockStore, \
             patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
            mgr_mock = MagicMock()
            mgr_mock._find_snapshot.return_value = snap_file
            MockStore.return_value = MagicMock()
            with patch("aictl.cmd.snapshot.SnapshotManager", return_value=mgr_mock):
                args = argparse.Namespace(id="snap_001", state_dir=None,
                                          output=out_file, json=True)
                ret = run_export(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["exported"])
        exported_data = json.loads(pathlib.Path(out_file).read_text())
        self.assertEqual(exported_data["snapshot_id"], snap_data["snapshot_id"])

    def test_run_export_not_found(self):
        from aictl.cmd.snapshot import run_export
        with patch("aictl.cmd.snapshot.StateStore"), \
             patch("aictl.cmd.snapshot.SnapshotManager") as MockMgr:
            MockMgr.return_value._find_snapshot.return_value = None
            args = argparse.Namespace(id="nonexistent", state_dir=None,
                                      output="", json=False)
            ret = run_export(args)
        self.assertEqual(ret, 1)

    def test_run_import_success(self):
        from aictl.cmd.snapshot import run_import
        snap_data = self._make_snap_data()
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        snap_file = tmpdir / "import_test.json"
        snap_file.write_text(json.dumps(snap_data))
        snap_id = snap_data["snapshot_id"]
        captured = []
        with patch("aictl.cmd.snapshot.StateStore") as MockStore, \
             patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
            mgr_mock = MagicMock()
            mgr_mock.snap_dir = tmpdir
            MockStore.return_value = MagicMock()
            with patch("aictl.cmd.snapshot.SnapshotManager", return_value=mgr_mock):
                args = argparse.Namespace(file=str(snap_file), state_dir=None,
                                          restore=False, json=True)
                ret = run_import(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["imported"])
        self.assertEqual(captured[0]["snapshot_id"], snap_id)

    def test_run_import_missing_file(self):
        from aictl.cmd.snapshot import run_import
        args = argparse.Namespace(file="/nonexistent/snap.json", state_dir=None,
                                  restore=False, json=False)
        ret = run_import(args)
        self.assertEqual(ret, 1)

    def test_run_import_invalid_json(self):
        from aictl.cmd.snapshot import run_import
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        bad_file = tmpdir / "bad.json"
        bad_file.write_text("not json")
        args = argparse.Namespace(file=str(bad_file), state_dir=None,
                                  restore=False, json=False)
        ret = run_import(args)
        self.assertEqual(ret, 1)

    def test_run_import_with_restore(self):
        from aictl.cmd.snapshot import run_import
        snap_data = self._make_snap_data()
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        snap_file = tmpdir / "restore_test.json"
        snap_file.write_text(json.dumps(snap_data))
        captured = []
        with patch("aictl.cmd.snapshot.StateStore"), \
             patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
            mgr_mock = MagicMock()
            mgr_mock.snap_dir = tmpdir
            mgr_mock.restore.return_value = (True, "Restored OK")
            with patch("aictl.cmd.snapshot.SnapshotManager", return_value=mgr_mock):
                args = argparse.Namespace(file=str(snap_file), state_dir=None,
                                          restore=True, json=True)
                ret = run_import(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["restored"])
        mgr_mock.restore.assert_called_once()


if __name__ == "__main__":
    unittest.main()
