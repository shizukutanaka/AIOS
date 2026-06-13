"""Pass 79 regression tests: context switch/export/import, warmup schedule/cancel/status,
config export/import."""

from __future__ import annotations

import argparse
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock


# ── context switch / export / import ─────────────────────────────────────────

class TestContextSwitchExportImport(unittest.TestCase):

    def _make_parser(self):
        from aictl.cmd.context import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_switch_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["context", "switch", "abc123"])
        self.assertEqual(args.func.__name__, "run_switch")
        self.assertEqual(args.snapshot_id, "abc123")

    def test_export_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["context", "export", "abc123"])
        self.assertEqual(args.func.__name__, "run_export")

    def test_export_output_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["context", "export", "abc123", "--output", "snap.json"])
        self.assertEqual(args.output, "snap.json")

    def test_import_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["context", "import", "/tmp/snap.json"])
        self.assertEqual(args.func.__name__, "run_import")

    def test_run_switch_not_found(self):
        from aictl.cmd.context import run_switch
        with patch("aictl.cmd.context.ContextContinuityEngine") as MockEng:
            MockEng.return_value.list_snapshots.return_value = []
            args = argparse.Namespace(snapshot_id="notexist", state_dir=None, json=True)
            ret = run_switch(args)
        self.assertEqual(ret, 1)

    def test_run_switch_found_json(self):
        from aictl.cmd.context import run_switch
        from aictl.runtime.continuity import ContextSnapshot
        snap = ContextSnapshot(
            snapshot_id="abc123def456", engine="vllm", model="llama3:8b",
            num_entries=5, status="saved", created_at=time.time(),
        )
        captured = []
        with patch("aictl.cmd.context.ContextContinuityEngine") as MockEng, \
             patch("aictl.cmd.context.load_config") as mock_cfg, \
             patch("aictl.cmd.context.print_json", side_effect=captured.append):
            MockEng.return_value.list_snapshots.return_value = [snap]
            MockEng.return_value._restore_engine_context.return_value = None
            mock_cfg.return_value.engines.to_dict.return_value = {"vllm": "http://localhost:8000"}
            args = argparse.Namespace(snapshot_id="abc123", state_dir=None, json=True)
            ret = run_switch(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["snapshot_id"], "abc123def456")
        self.assertTrue(captured[0]["switched"])

    def test_run_export_not_found(self):
        from aictl.cmd.context import run_export
        with patch("aictl.cmd.context.ContextContinuityEngine") as MockEng:
            MockEng.return_value.list_snapshots.return_value = []
            args = argparse.Namespace(snapshot_id="ghost", output="", state_dir=None, json=False)
            ret = run_export(args)
        self.assertEqual(ret, 1)

    def test_run_export_writes_file(self):
        from aictl.cmd.context import run_export
        from aictl.runtime.continuity import ContextSnapshot
        snap = ContextSnapshot(
            snapshot_id="xyz789", engine="ollama", model="phi3",
            num_entries=2, status="saved", created_at=1000.0,
        )
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        out = str(tmpdir / "snap.json")
        captured = []
        with patch("aictl.cmd.context.ContextContinuityEngine") as MockEng, \
             patch("aictl.cmd.context.print_json", side_effect=captured.append):
            MockEng.return_value.list_snapshots.return_value = [snap]
            args = argparse.Namespace(snapshot_id="xyz789", output=out, state_dir=None, json=True)
            ret = run_export(args)
        self.assertEqual(ret, 0)
        self.assertTrue(pathlib.Path(out).exists())
        data = json.loads(pathlib.Path(out).read_text())
        self.assertEqual(data["snapshot_id"], "xyz789")
        self.assertTrue(captured[0]["exported"])

    def test_run_import_missing_file(self):
        from aictl.cmd.context import run_import
        args = argparse.Namespace(file="/nonexistent/snap.json", state_dir=None, json=False)
        ret = run_import(args)
        self.assertEqual(ret, 1)

    def test_run_import_adds_to_index(self):
        from aictl.cmd.context import run_import
        from aictl.runtime.continuity import ContextSnapshot
        snap_data = {
            "snapshot_id": "import001", "engine": "sglang", "model": "mistral:7b",
            "num_entries": 3, "status": "saved", "created_at": 2000.0,
        }
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        f = tmpdir / "import.json"
        f.write_text(json.dumps(snap_data))
        captured = []
        with patch("aictl.cmd.context.ContextContinuityEngine") as MockEng, \
             patch("aictl.cmd.context.print_json", side_effect=captured.append):
            MockEng.return_value.list_snapshots.return_value = []
            MockEng.return_value._save_index.return_value = None
            args = argparse.Namespace(file=str(f), state_dir=None, json=True)
            ret = run_import(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["imported"])
        self.assertEqual(captured[0]["snapshot_id"], "import001")

    def test_run_import_bad_json(self):
        from aictl.cmd.context import run_import
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        f = tmpdir / "bad.json"
        f.write_text("not json!!!")
        args = argparse.Namespace(file=str(f), state_dir=None, json=False)
        ret = run_import(args)
        self.assertEqual(ret, 1)

    def test_run_import_missing_required_fields(self):
        from aictl.cmd.context import run_import
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        f = tmpdir / "nofields.json"
        f.write_text(json.dumps({"foo": "bar"}))
        args = argparse.Namespace(file=str(f), state_dir=None, json=False)
        ret = run_import(args)
        self.assertEqual(ret, 1)


# ── warmup schedule / cancel / status ────────────────────────────────────────

class TestWarmupSchedule(unittest.TestCase):

    def _make_parser(self):
        from aictl.cmd.warmup import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_schedule_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["warmup", "schedule"])
        self.assertEqual(args.func.__name__, "run_schedule")

    def test_schedule_every_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["warmup", "schedule", "--every", "30m"])
        self.assertEqual(args.every, "30m")

    def test_cancel_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["warmup", "cancel"])
        self.assertEqual(args.func.__name__, "run_cancel")

    def test_status_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["warmup", "status"])
        self.assertEqual(args.func.__name__, "run_schedule_status")

    def test_run_schedule_creates_file(self):
        from aictl.cmd.warmup import run_schedule
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        captured = []
        with patch("aictl.cmd.warmup.print_json", side_effect=captured.append):
            args = argparse.Namespace(every="1h", top=3, state_dir=str(tmpdir), json=True)
            ret = run_schedule(args)
        self.assertEqual(ret, 0)
        schedule_file = tmpdir / "warmup_schedule.json"
        self.assertTrue(schedule_file.exists())
        data = json.loads(schedule_file.read_text())
        self.assertEqual(data["every"], "1h")
        self.assertEqual(data["top"], 3)
        self.assertIn("next_run", data)

    def test_run_schedule_json_output(self):
        from aictl.cmd.warmup import run_schedule
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        captured = []
        with patch("aictl.cmd.warmup.print_json", side_effect=captured.append):
            args = argparse.Namespace(every="30m", top=5, state_dir=str(tmpdir), json=True)
            ret = run_schedule(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["every"], "30m")
        self.assertEqual(captured[0]["top"], 5)
        self.assertIn("interval_secs", captured[0])
        self.assertEqual(captured[0]["interval_secs"], 1800)

    def test_run_cancel_no_file(self):
        from aictl.cmd.warmup import run_cancel
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        args = argparse.Namespace(state_dir=str(tmpdir), json=False)
        ret = run_cancel(args)
        self.assertEqual(ret, 0)  # no-op, no error

    def test_run_cancel_removes_file(self):
        from aictl.cmd.warmup import run_schedule, run_cancel
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        run_schedule(argparse.Namespace(every="1h", top=3, state_dir=str(tmpdir), json=False))
        schedule_file = tmpdir / "warmup_schedule.json"
        self.assertTrue(schedule_file.exists())
        ret = run_cancel(argparse.Namespace(state_dir=str(tmpdir), json=False))
        self.assertEqual(ret, 0)
        self.assertFalse(schedule_file.exists())

    def test_run_status_no_schedule(self):
        from aictl.cmd.warmup import run_schedule_status
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        args = argparse.Namespace(state_dir=str(tmpdir), json=False)
        ret = run_schedule_status(args)
        self.assertEqual(ret, 0)

    def test_run_status_json_with_schedule(self):
        from aictl.cmd.warmup import run_schedule, run_schedule_status
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        run_schedule(argparse.Namespace(every="6h", top=2, state_dir=str(tmpdir), json=False))
        captured = []
        with patch("aictl.cmd.warmup.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=str(tmpdir), json=True)
            ret = run_schedule_status(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["every"], "6h")
        self.assertIn("remaining_secs", captured[0])
        self.assertGreater(captured[0]["remaining_secs"], 0)

    def test_parse_interval_secs(self):
        from aictl.cmd.warmup import _parse_interval_secs
        self.assertEqual(_parse_interval_secs("30m"), 1800)
        self.assertEqual(_parse_interval_secs("1h"), 3600)
        self.assertEqual(_parse_interval_secs("2h"), 7200)
        self.assertEqual(_parse_interval_secs("1d"), 86400)
        # fallback
        self.assertEqual(_parse_interval_secs("bad"), 3600)


# ── config export / import ────────────────────────────────────────────────────

class TestConfigExportImport(unittest.TestCase):

    def _make_parser(self):
        from aictl.cmd.config import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_export_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["config", "export"])
        self.assertEqual(args.func.__name__, "run_export")

    def test_export_output_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["config", "export", "--output", "myconfig.json"])
        self.assertEqual(args.output, "myconfig.json")

    def test_import_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["config", "import", "/tmp/cfg.json"])
        self.assertEqual(args.func.__name__, "run_import")

    def test_run_export_writes_file(self):
        from aictl.cmd.config import run_export
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        out = str(tmpdir / "export.json")
        captured = []
        with patch("aictl.cmd.config.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=None, output=out, json=True)
            ret = run_export(args)
        self.assertEqual(ret, 0)
        self.assertTrue(pathlib.Path(out).exists())
        data = json.loads(pathlib.Path(out).read_text())
        self.assertIn("engines", data)
        self.assertIn("slo", data)
        self.assertTrue(captured[0]["exported"])
        self.assertEqual(captured[0]["output"], out)

    def test_run_export_default_filename(self):
        from aictl.cmd.config import run_export
        import os
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        orig_dir = os.getcwd()
        try:
            os.chdir(str(tmpdir))
            args = argparse.Namespace(state_dir=None, output="", json=False)
            ret = run_export(args)
            self.assertEqual(ret, 0)
            self.assertTrue((tmpdir / "aios-config.json").exists())
        finally:
            os.chdir(orig_dir)

    def test_run_import_missing_file(self):
        from aictl.cmd.config import run_import
        args = argparse.Namespace(file="/nonexistent/cfg.json", state_dir=None, json=False)
        ret = run_import(args)
        self.assertEqual(ret, 1)

    def test_run_import_bad_json(self):
        from aictl.cmd.config import run_import
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        f = tmpdir / "bad.json"
        f.write_text("not json")
        args = argparse.Namespace(file=str(f), state_dir=None, json=False)
        ret = run_import(args)
        self.assertEqual(ret, 1)

    def test_run_export_import_roundtrip(self):
        """Export then re-import should produce the same config."""
        from aictl.cmd.config import run_export, run_import, run_show
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        cfg_file = str(tmpdir / "roundtrip.json")
        # Export
        ret = run_export(argparse.Namespace(state_dir=None, output=cfg_file, json=False))
        self.assertEqual(ret, 0)
        # Import into different state_dir
        ret = run_import(argparse.Namespace(file=cfg_file, state_dir=str(tmpdir), json=False))
        self.assertEqual(ret, 0)
        # Re-export to verify
        cfg_file2 = str(tmpdir / "roundtrip2.json")
        ret = run_export(argparse.Namespace(state_dir=str(tmpdir), output=cfg_file2, json=False))
        self.assertEqual(ret, 0)
        d1 = json.loads(pathlib.Path(cfg_file).read_text())
        d2 = json.loads(pathlib.Path(cfg_file2).read_text())
        self.assertEqual(d1, d2)

    def test_run_import_json_output(self):
        from aictl.cmd.config import run_export, run_import
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        cfg_file = str(tmpdir / "cfg.json")
        run_export(argparse.Namespace(state_dir=None, output=cfg_file, json=False))
        captured = []
        with patch("aictl.cmd.config.print_json", side_effect=captured.append):
            args = argparse.Namespace(file=cfg_file, state_dir=str(tmpdir), json=True)
            ret = run_import(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["imported"])
        self.assertEqual(captured[0]["file"], cfg_file)


if __name__ == "__main__":
    unittest.main()
