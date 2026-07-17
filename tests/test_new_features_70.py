"""Pass 70 regression tests: events CLI, daemon management, model inspect."""

from __future__ import annotations

import argparse
import time
import unittest
from unittest.mock import patch, MagicMock


# ── events ────────────────────────────────────────────────────────────────────

class TestEventsCommand(unittest.TestCase):
    """aictl events — query and stream the AIOS event bus."""

    def _make_parser(self):
        from aictl.cmd.events import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_list_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["events", "list"])
        self.assertEqual(args.func.__name__, "run_list")

    def test_watch_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["events", "watch"])
        self.assertEqual(args.func.__name__, "run_watch")

    def test_clear_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["events", "clear"])
        self.assertEqual(args.func.__name__, "run_clear")

    def test_types_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["events", "types"])
        self.assertEqual(args.func.__name__, "run_types")

    def test_list_limit_default(self):
        parser = self._make_parser()
        args = parser.parse_args(["events", "list"])
        self.assertEqual(args.limit, 20)

    def test_list_limit_custom(self):
        parser = self._make_parser()
        args = parser.parse_args(["events", "list", "--limit", "5"])
        self.assertEqual(args.limit, 5)

    def test_list_type_filter(self):
        parser = self._make_parser()
        args = parser.parse_args(["events", "list", "--type", "stack.applied"])
        self.assertEqual(args.event_type, "stack.applied")

    def test_run_list_empty_bus(self):
        from aictl.cmd.events import run_list
        from aictl.core.events import EventBus
        bus = EventBus()
        with patch("aictl.cmd.events.get_bus", return_value=bus):
            args = argparse.Namespace(limit=20, event_type="", json=False)
            ret = run_list(args)
        self.assertEqual(ret, 0)

    def test_run_list_json(self):
        from aictl.cmd.events import run_list
        from aictl.core.events import EventBus, Event
        bus = EventBus()
        bus.publish(Event(type="stack.applied", source="test", data={"name": "foo"}))
        captured = []
        with patch("aictl.cmd.events.get_bus", return_value=bus), \
             patch("aictl.cmd.events.print_json", side_effect=captured.append):
            args = argparse.Namespace(limit=20, event_type="", json=True)
            ret = run_list(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured), 1)
        self.assertIsInstance(captured[0], list)
        self.assertEqual(captured[0][0]["type"], "stack.applied")

    def test_run_list_type_filter_applied(self):
        from aictl.cmd.events import run_list
        from aictl.core.events import EventBus, Event
        bus = EventBus()
        bus.publish(Event(type="stack.applied", source="a"))
        bus.publish(Event(type="engine.degraded", source="b"))
        captured = []
        with patch("aictl.cmd.events.get_bus", return_value=bus), \
             patch("aictl.cmd.events.print_json", side_effect=captured.append):
            args = argparse.Namespace(limit=20, event_type="stack.applied", json=True)
            ret = run_list(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 1)
        self.assertEqual(captured[0][0]["type"], "stack.applied")

    def test_run_clear(self):
        from aictl.cmd.events import run_clear
        from aictl.core.events import EventBus, Event
        bus = EventBus()
        bus.publish(Event(type="stack.applied"))
        self.assertEqual(len(bus.recent()), 1)
        with patch("aictl.cmd.events.get_bus", return_value=bus):
            args = argparse.Namespace()
            ret = run_clear(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(bus.recent()), 0)

    def test_run_types_json(self):
        from aictl.cmd.events import run_types
        captured = []
        with patch("aictl.cmd.events.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True)
            ret = run_types(args)
        self.assertEqual(ret, 0)
        self.assertIn("stack.applied", captured[0])
        self.assertIn("engine.degraded", captured[0])

    def test_events_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["events", "list"])
        self.assertEqual(args.func.__name__, "run_list")


# ── daemon ────────────────────────────────────────────────────────────────────

class TestDaemonCommand(unittest.TestCase):
    """aictl daemon — manage the aiosd background daemon."""

    def _make_parser(self):
        from aictl.cmd.daemon import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_status_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["daemon", "status"])
        self.assertEqual(args.func.__name__, "run_status")

    def test_stop_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["daemon", "stop"])
        self.assertEqual(args.func.__name__, "run_stop")

    def test_restart_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["daemon", "restart"])
        self.assertEqual(args.func.__name__, "run_restart")

    def test_logs_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["daemon", "logs"])
        self.assertEqual(args.func.__name__, "run_logs")

    def test_run_status_not_running(self):
        from aictl.cmd.daemon import run_status
        with patch("aictl.cmd.daemon._query_health", return_value=None):
            args = argparse.Namespace(host="localhost", port=7700, json=False)
            ret = run_status(args)
        self.assertEqual(ret, 1)

    def test_run_status_running_json(self):
        from aictl.cmd.daemon import run_status
        health = {"status": "ok", "uptime_seconds": 42.0, "profile": "cpu-only",
                  "initialized": True, "container_runtime": "podman"}
        captured = []
        with patch("aictl.cmd.daemon._query_health", return_value=health), \
             patch("aictl.cmd.daemon._find_daemon_pid", return_value=12345), \
             patch("aictl.cmd.daemon.print_json", side_effect=captured.append):
            args = argparse.Namespace(host="localhost", port=7700, json=True)
            ret = run_status(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["running"])
        self.assertEqual(captured[0]["pid"], 12345)

    def test_run_status_not_running_json(self):
        from aictl.cmd.daemon import run_status
        captured = []
        with patch("aictl.cmd.daemon._query_health", return_value=None), \
             patch("aictl.cmd.daemon.print_json", side_effect=captured.append):
            args = argparse.Namespace(host="localhost", port=7700, json=True)
            ret = run_status(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["running"])

    def test_run_stop_no_pid(self):
        from aictl.cmd.daemon import run_stop
        with patch("aictl.cmd.daemon._find_daemon_pid", return_value=None):
            args = argparse.Namespace(host="localhost", port=7700)
            ret = run_stop(args)
        self.assertEqual(ret, 1)

    def test_run_stop_sends_sigterm(self):
        from aictl.cmd.daemon import run_stop
        import signal
        killed = []
        with patch("aictl.cmd.daemon._find_daemon_pid", return_value=9999), \
             patch("os.kill", side_effect=lambda pid, sig: killed.append((pid, sig))):
            args = argparse.Namespace(host="localhost", port=7700)
            ret = run_stop(args)
        self.assertEqual(ret, 0)
        self.assertEqual(killed, [(9999, signal.SIGTERM)])

    def test_run_logs_no_file(self):
        from aictl.cmd.daemon import run_logs
        from pathlib import Path
        with patch("aictl.cmd.daemon.DEFAULT_STATE_DIR", Path("/nonexistent-state-dir-xyz")):
            args = argparse.Namespace(lines=50, json=False)
            ret = run_logs(args)
        self.assertEqual(ret, 0)

    def test_run_logs_with_file(self):
        from aictl.cmd.daemon import run_logs
        import tempfile
        import pathlib
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        log = tmpdir / "daemon.log"
        log.write_text("line1\nline2\nline3\n")
        with patch("aictl.cmd.daemon.DEFAULT_STATE_DIR", tmpdir):
            args = argparse.Namespace(lines=50, json=False)
            lines_out = []
            with patch("builtins.print", side_effect=lambda *a, **k: lines_out.append(a[0])):
                ret = run_logs(args)
        self.assertEqual(ret, 0)
        self.assertIn("line1", lines_out)

    def test_daemon_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["daemon", "status"])
        self.assertEqual(args.func.__name__, "run_status")


# ── model inspect ──────────────────────────────────────────────────────────────

class TestModelInspect(unittest.TestCase):
    """model inspect — show full metadata for a registered model."""

    def _make_parser(self):
        from aictl.cmd.model import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_inspect_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["model", "inspect", "llama3"])
        self.assertEqual(args.func.__name__, "run_inspect")
        self.assertEqual(args.name, "llama3")

    def test_run_inspect_not_found(self):
        from aictl.cmd.model import run_inspect
        with patch("aictl.cmd.model.StateStore") as MockStore:
            MockStore.return_value.list_models.return_value = []
            args = argparse.Namespace(name="ghost", state_dir=None, json=False)
            ret = run_inspect(args)
        self.assertEqual(ret, 1)

    def test_run_inspect_not_found_json(self):
        from aictl.cmd.model import run_inspect
        captured = []
        with patch("aictl.cmd.model.StateStore") as MockStore, \
             patch("aictl.cmd.model.print_json", side_effect=captured.append):
            MockStore.return_value.list_models.return_value = []
            args = argparse.Namespace(name="ghost", state_dir=None, json=True)
            ret = run_inspect(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["found"])

    def test_run_inspect_found_by_name(self):
        from aictl.cmd.model import run_inspect
        model_row = {
            "id": "abc12345", "name": "llama3", "format": "gguf",
            "status": "available", "size_bytes": 4_000_000_000,
            "signed": 0, "signer": "", "digest": "sha256:abc", "registered_at": time.time(),
        }
        captured = []
        with patch("aictl.cmd.model.StateStore") as MockStore, \
             patch("aictl.cmd.model.print_json", side_effect=captured.append):
            MockStore.return_value.list_models.return_value = [model_row]
            args = argparse.Namespace(name="llama3", state_dir=None, json=True)
            ret = run_inspect(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["name"], "llama3")
        self.assertEqual(captured[0]["id"], "abc12345")

    def test_run_inspect_found_by_id_prefix(self):
        from aictl.cmd.model import run_inspect
        model_row = {
            "id": "abc12345", "name": "llama3", "format": "gguf",
            "status": "available", "size_bytes": 0,
            "signed": 0, "signer": "", "digest": "", "registered_at": time.time(),
        }
        captured = []
        with patch("aictl.cmd.model.StateStore") as MockStore, \
             patch("aictl.cmd.model.print_json", side_effect=captured.append):
            MockStore.return_value.list_models.return_value = [model_row]
            args = argparse.Namespace(name="abc123", state_dir=None, json=True)
            ret = run_inspect(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["id"], "abc12345")

    def test_run_inspect_text_output(self):
        from aictl.cmd.model import run_inspect
        model_row = {
            "id": "abc12345", "name": "llama3", "format": "gguf",
            "status": "available", "size_bytes": 4_000_000_000,
            "signed": 1, "signer": "ci@example.com",
            "digest": "sha256:abc", "registered_at": time.time(),
        }
        lines = []
        with patch("aictl.cmd.model.StateStore") as MockStore:
            MockStore.return_value.list_models.return_value = [model_row]
            with patch("builtins.print", side_effect=lambda *a, **k: lines.append(a[0] if a else "")):
                args = argparse.Namespace(name="llama3", state_dir=None, json=False)
                ret = run_inspect(args)
        self.assertEqual(ret, 0)
        combined = "\n".join(lines)
        self.assertIn("llama3", combined)
        self.assertIn("gguf", combined)
        self.assertIn("GB", combined)


if __name__ == "__main__":
    unittest.main()
