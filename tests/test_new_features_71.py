"""Pass 71 regression tests: engines command, logs enhancements, audit enhancements."""

from __future__ import annotations

import argparse
import time
import unittest
from unittest.mock import patch, MagicMock


# ── engines ───────────────────────────────────────────────────────────────────

class TestEnginesCommand(unittest.TestCase):
    """aictl engines — discover and inspect inference engines."""

    def _make_parser(self):
        from aictl.cmd.engines import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def _mock_healths(self):
        from aictl.runtime.adapters import EngineHealth
        return [
            EngineHealth(engine="vllm", endpoint="http://localhost:8000",
                         reachable=True, status="READY", models=["llama3"],
                         version="0.19", latency_ms=12.5),
            EngineHealth(engine="ollama", endpoint="http://localhost:11434",
                         reachable=False, status="OFFLINE", models=[],
                         error="connection refused"),
        ]

    def test_list_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["engines", "list"])
        self.assertEqual(args.func.__name__, "run_list")

    def test_health_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["engines", "health"])
        self.assertEqual(args.func.__name__, "run_health")

    def test_models_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["engines", "models"])
        self.assertEqual(args.func.__name__, "run_models")

    def test_run_list_json(self):
        from aictl.cmd.engines import run_list
        healths = self._mock_healths()
        captured = []
        with patch("aictl.cmd.engines.discover_engines", return_value=healths), \
             patch("aictl.cmd.engines.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=None, json=True)
            ret = run_list(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 2)
        self.assertEqual(captured[0][0]["engine"], "vllm")
        self.assertTrue(captured[0][0]["reachable"])
        self.assertFalse(captured[0][1]["reachable"])

    def test_run_list_shows_model_count(self):
        from aictl.cmd.engines import run_list
        healths = self._mock_healths()
        captured = []
        with patch("aictl.cmd.engines.discover_engines", return_value=healths), \
             patch("aictl.cmd.engines.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=None, json=True)
            run_list(args)
        self.assertEqual(captured[0][0]["models"], 1)

    def test_run_health_json(self):
        from aictl.cmd.engines import run_health
        healths = self._mock_healths()
        captured = []
        with patch("aictl.cmd.engines.discover_engines", return_value=healths), \
             patch("aictl.cmd.engines.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=None, engine="", json=True)
            ret = run_health(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 2)
        self.assertIn("llama3", captured[0][0]["models"])
        self.assertEqual(captured[0][0]["version"], "0.19")

    def test_run_health_filter_by_engine(self):
        from aictl.cmd.engines import run_health
        healths = self._mock_healths()
        captured = []
        with patch("aictl.cmd.engines.discover_engines", return_value=healths), \
             patch("aictl.cmd.engines.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=None, engine="vllm", json=True)
            ret = run_health(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 1)
        self.assertEqual(captured[0][0]["engine"], "vllm")

    def test_run_health_unknown_engine(self):
        from aictl.cmd.engines import run_health
        with patch("aictl.cmd.engines.discover_engines", return_value=self._mock_healths()):
            args = argparse.Namespace(state_dir=None, engine="trt-llm", json=False)
            ret = run_health(args)
        self.assertEqual(ret, 1)

    def test_run_models_json(self):
        from aictl.cmd.engines import run_models
        healths = self._mock_healths()
        captured = []
        with patch("aictl.cmd.engines.discover_engines", return_value=healths), \
             patch("aictl.cmd.engines.print_json", side_effect=captured.append):
            args = argparse.Namespace(state_dir=None, json=True)
            ret = run_models(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 1)
        self.assertEqual(captured[0][0]["model"], "llama3")
        self.assertEqual(captured[0][0]["engine"], "vllm")

    def test_engines_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["engines", "list"])
        self.assertEqual(args.func.__name__, "run_list")


# ── logs enhancements ─────────────────────────────────────────────────────────

class TestLogsEnhancements(unittest.TestCase):
    """logs --since/--level/--grep flags."""

    def _make_parser(self):
        from aictl.cmd.logs import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_since_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["logs", "myservice", "--since", "5m"])
        self.assertEqual(args.since, "5m")

    def test_level_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["logs", "myservice", "--level", "ERROR"])
        self.assertEqual(args.level, "ERROR")

    def test_grep_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["logs", "myservice", "--grep", "timeout"])
        self.assertEqual(args.grep, "timeout")

    def test_parse_since_minutes(self):
        from aictl.cmd.logs import _parse_since
        self.assertEqual(_parse_since("5m"), "5m")

    def test_parse_since_empty(self):
        from aictl.cmd.logs import _parse_since
        self.assertEqual(_parse_since(""), "")

    def test_parse_since_hours(self):
        from aictl.cmd.logs import _parse_since
        self.assertEqual(_parse_since("2h"), "2h")

    def test_grep_filters_lines(self):
        from aictl.cmd.logs import run
        import subprocess
        fake_output = "INFO starting\nERROR timeout occurred\nINFO done\n"
        fake_proc = MagicMock()
        fake_proc.stdout = fake_output
        fake_proc.stderr = ""
        fake_proc.returncode = 0
        printed = []
        with patch("aictl.cmd.logs.detect_container_runtime", return_value="podman"), \
             patch("subprocess.run", return_value=fake_proc), \
             patch("builtins.print", side_effect=lambda *a, **k: printed.append(a[0] if a else "")):
            args = argparse.Namespace(service="myservice", follow=False,
                                      tail="50", since="", level="", grep="timeout")
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(printed), 1)
        self.assertIn("timeout", printed[0])

    def test_level_filters_lines(self):
        from aictl.cmd.logs import run
        fake_output = "INFO starting\nERROR timeout occurred\nINFO done\n"
        fake_proc = MagicMock()
        fake_proc.stdout = fake_output
        fake_proc.stderr = ""
        fake_proc.returncode = 0
        printed = []
        with patch("aictl.cmd.logs.detect_container_runtime", return_value="podman"), \
             patch("subprocess.run", return_value=fake_proc), \
             patch("builtins.print", side_effect=lambda *a, **k: printed.append(a[0] if a else "")):
            args = argparse.Namespace(service="myservice", follow=False,
                                      tail="50", since="", level="ERROR", grep="")
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(printed), 1)
        self.assertIn("ERROR", printed[0])


# ── audit enhancements ────────────────────────────────────────────────────────

class TestAuditEnhancements(unittest.TestCase):
    """audit --since/--resource/--actor/--export flags."""

    def _make_parser(self):
        from aictl.cmd.audit import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_since_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["audit", "--since", "1h"])
        self.assertEqual(args.since, "1h")

    def test_resource_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["audit", "--resource", "llama3"])
        self.assertEqual(args.resource, "llama3")

    def test_actor_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["audit", "--actor", "system"])
        self.assertEqual(args.actor, "system")

    def test_export_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["audit", "--export", "/tmp/out.json"])
        self.assertEqual(args.export, "/tmp/out.json")

    def test_parse_since_minutes(self):
        from aictl.cmd.audit import _parse_since
        before = time.time()
        ts = _parse_since("5m")
        after = time.time()
        self.assertAlmostEqual(ts, before - 300, delta=5)

    def test_parse_since_hours(self):
        from aictl.cmd.audit import _parse_since
        ts = _parse_since("2h")
        expected = time.time() - 7200
        self.assertAlmostEqual(ts, expected, delta=5)

    def test_parse_since_empty(self):
        from aictl.cmd.audit import _parse_since
        self.assertEqual(_parse_since(""), 0.0)

    def test_resource_filter(self):
        from aictl.cmd.audit import run
        from aictl.core.audit import AuditEntry
        entries = [
            AuditEntry(event="model.loaded", resource="llama3", actor="system",
                       action="create", outcome="success", timestamp=time.time()),
            AuditEntry(event="stack.applied", resource="local-chat", actor="user",
                       action="apply", outcome="success", timestamp=time.time()),
        ]
        captured = []
        with patch("aictl.cmd.audit.get_audit_log") as mock_log, \
             patch("aictl.cmd.audit.print_json", side_effect=captured.append):
            mock_log.return_value.read.return_value = entries
            args = argparse.Namespace(state_dir=None, lines=20, event="",
                                      since="", resource="llama3", actor="",
                                      export="", json=True)
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 1)
        self.assertEqual(captured[0][0]["resource"], "llama3")

    def test_actor_filter(self):
        from aictl.cmd.audit import run
        from aictl.core.audit import AuditEntry
        entries = [
            AuditEntry(event="model.loaded", resource="llama3", actor="system",
                       action="create", outcome="success", timestamp=time.time()),
            AuditEntry(event="stack.applied", resource="chat", actor="user",
                       action="apply", outcome="success", timestamp=time.time()),
        ]
        captured = []
        with patch("aictl.cmd.audit.get_audit_log") as mock_log, \
             patch("aictl.cmd.audit.print_json", side_effect=captured.append):
            mock_log.return_value.read.return_value = entries
            args = argparse.Namespace(state_dir=None, lines=20, event="",
                                      since="", resource="", actor="user",
                                      export="", json=True)
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 1)
        self.assertEqual(captured[0][0]["actor"], "user")

    def test_since_filter(self):
        from aictl.cmd.audit import run
        from aictl.core.audit import AuditEntry
        old_ts = time.time() - 7200  # 2 hours ago
        new_ts = time.time() - 60    # 1 minute ago
        entries = [
            AuditEntry(event="model.loaded", resource="x", actor="system",
                       action="create", outcome="success", timestamp=old_ts),
            AuditEntry(event="stack.applied", resource="y", actor="user",
                       action="apply", outcome="success", timestamp=new_ts),
        ]
        captured = []
        with patch("aictl.cmd.audit.get_audit_log") as mock_log, \
             patch("aictl.cmd.audit.print_json", side_effect=captured.append):
            mock_log.return_value.read.return_value = entries
            args = argparse.Namespace(state_dir=None, lines=20, event="",
                                      since="30m", resource="", actor="",
                                      export="", json=True)
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured[0]), 1)
        self.assertAlmostEqual(captured[0][0]["timestamp"], new_ts, delta=1)

    def test_export_writes_file(self):
        from aictl.cmd.audit import run
        from aictl.core.audit import AuditEntry
        import tempfile, pathlib, json
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        out = str(tmpdir / "audit_export.json")
        entries = [
            AuditEntry(event="model.loaded", resource="llama3", actor="system",
                       action="create", outcome="success", timestamp=time.time()),
        ]
        with patch("aictl.cmd.audit.get_audit_log") as mock_log:
            mock_log.return_value.read.return_value = entries
            args = argparse.Namespace(state_dir=None, lines=20, event="",
                                      since="", resource="", actor="",
                                      export=out, json=False)
            ret = run(args)
        self.assertEqual(ret, 0)
        data = json.loads(pathlib.Path(out).read_text())
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["event"], "model.loaded")


if __name__ == "__main__":
    unittest.main()
