"""Pass 81 regression tests: audit stats/purge, recipe test/export, bench history."""

from __future__ import annotations

import argparse
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock


# ── audit stats / purge ───────────────────────────────────────────────────────

class TestAuditStatsPurge(unittest.TestCase):

    def _make_parser(self):
        from aictl.cmd.audit import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_stats_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["audit", "stats"])
        self.assertEqual(args.func.__name__, "run_stats")

    def test_stats_since_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["audit", "stats", "--since", "24h"])
        self.assertEqual(args.stats_since, "24h")

    def test_purge_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["audit", "purge"])
        self.assertEqual(args.func.__name__, "run_purge")

    def test_purge_max_age_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["audit", "purge", "--max-age", "14"])
        self.assertEqual(args.max_age, 14)

    def test_purge_dry_run_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["audit", "purge", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_root_audit_still_works(self):
        """Ensure existing root-level audit command still functions."""
        parser = self._make_parser()
        args = parser.parse_args(["audit", "--lines", "5"])
        self.assertEqual(args.func.__name__, "run")
        self.assertEqual(args.lines, 5)

    def test_run_stats_empty(self):
        from aictl.cmd.audit import run_stats
        captured = []
        with patch("aictl.cmd.audit.get_audit_log") as mock_log, \
             patch("aictl.cmd.audit.print_json", side_effect=captured.append):
            mock_log.return_value.read.return_value = []
            args = argparse.Namespace(state_dir=None, stats_since="7d", top=10, json=True)
            ret = run_stats(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["total"], 0)
        self.assertEqual(captured[0]["top_events"], [])

    def test_run_stats_with_entries(self):
        from aictl.cmd.audit import run_stats
        from aictl.core.audit import AuditEntry
        now = time.time()
        entries = [
            AuditEntry(timestamp=now, event="model.loaded", resource="llama3:8b",
                       action="load", outcome="success", actor="system"),
            AuditEntry(timestamp=now, event="model.loaded", resource="phi3",
                       action="load", outcome="success", actor="system"),
            AuditEntry(timestamp=now, event="stack.applied", resource="local-chat",
                       action="apply", outcome="success", actor="user"),
        ]
        captured = []
        with patch("aictl.cmd.audit.get_audit_log") as mock_log, \
             patch("aictl.cmd.audit.print_json", side_effect=captured.append):
            mock_log.return_value.read.return_value = entries
            args = argparse.Namespace(state_dir=None, stats_since="7d", top=10, json=True)
            ret = run_stats(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["total"], 3)
        top_events = {e["event"]: e["count"] for e in captured[0]["top_events"]}
        self.assertEqual(top_events.get("model.loaded"), 2)
        self.assertEqual(top_events.get("stack.applied"), 1)

    def test_run_purge_no_files(self):
        from aictl.cmd.audit import run_purge
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        audit_dir = tmpdir / "audit"
        audit_dir.mkdir()
        args = argparse.Namespace(state_dir=str(tmpdir), max_age=30, dry_run=False, json=False)
        with patch("aictl.cmd.audit.get_audit_log") as mock_log:
            mock_inst = MagicMock()
            mock_inst.dir = audit_dir
            mock_log.return_value = mock_inst
            ret = run_purge(args)
        self.assertEqual(ret, 0)

    def test_run_purge_deletes_old_files_json(self):
        from aictl.cmd.audit import run_purge
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        audit_dir = tmpdir / "audit"
        audit_dir.mkdir()
        # Create an old audit file (mtime in the past)
        old_file = audit_dir / "audit-2020-01-01.jsonl"
        old_file.write_text('{"event":"test"}\n')
        import os
        old_time = time.time() - 40 * 86400  # 40 days ago
        os.utime(str(old_file), (old_time, old_time))

        captured = []
        with patch("aictl.cmd.audit.get_audit_log") as mock_log, \
             patch("aictl.cmd.audit.print_json", side_effect=captured.append):
            mock_inst = MagicMock()
            mock_inst.dir = audit_dir
            mock_log.return_value = mock_inst
            args = argparse.Namespace(state_dir=None, max_age=30, dry_run=False, json=True)
            ret = run_purge(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["purged"], 1)
        self.assertFalse(old_file.exists())

    def test_run_purge_dry_run_does_not_delete(self):
        from aictl.cmd.audit import run_purge
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        audit_dir = tmpdir / "audit"
        audit_dir.mkdir()
        old_file = audit_dir / "audit-2020-01-01.jsonl"
        old_file.write_text('{"event":"test"}\n')
        import os
        old_time = time.time() - 40 * 86400
        os.utime(str(old_file), (old_time, old_time))

        captured = []
        with patch("aictl.cmd.audit.get_audit_log") as mock_log, \
             patch("aictl.cmd.audit.print_json", side_effect=captured.append):
            mock_inst = MagicMock()
            mock_inst.dir = audit_dir
            mock_log.return_value = mock_inst
            args = argparse.Namespace(state_dir=None, max_age=30, dry_run=True, json=True)
            ret = run_purge(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["dry_run"])
        self.assertEqual(captured[0]["purged"], 0)
        self.assertTrue(old_file.exists())  # not deleted


# ── recipe test / export ──────────────────────────────────────────────────────

class TestRecipeTestExport(unittest.TestCase):

    def _make_parser(self):
        from aictl.cmd.recipe import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_test_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["recipe", "test", "local-chat"])
        self.assertEqual(args.func.__name__, "run_test")
        self.assertEqual(args.name, "local-chat")

    def test_export_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["recipe", "export", "local-chat"])
        self.assertEqual(args.func.__name__, "run_export")

    def test_export_output_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["recipe", "export", "local-chat", "--output", "out.json"])
        self.assertEqual(args.output, "out.json")

    def test_run_test_unknown_recipe(self):
        from aictl.cmd.recipe import run_test
        captured = []
        with patch("aictl.cmd.recipe.print_json", side_effect=captured.append):
            args = argparse.Namespace(name="no-such-recipe", state_dir=None, json=True)
            ret = run_test(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["passed"])

    def test_run_test_known_recipe_json(self):
        from aictl.cmd.recipe import run_test
        captured = []
        with patch("aictl.cmd.recipe.print_json", side_effect=captured.append):
            args = argparse.Namespace(name="local-chat", state_dir=None, json=True)
            ret = run_test(args)
        # May pass or fail depending on GPU availability, but should not raise
        self.assertIn("checks", captured[0])
        self.assertIn("passed", captured[0])
        checks_by_name = {c["check"]: c["passed"] for c in captured[0]["checks"]}
        self.assertIn("structural_validation", checks_by_name)
        # Structural check on local-chat should pass
        self.assertTrue(checks_by_name["structural_validation"])

    def test_run_test_dry_run_check_present(self):
        from aictl.cmd.recipe import run_test
        captured = []
        with patch("aictl.cmd.recipe.print_json", side_effect=captured.append):
            args = argparse.Namespace(name="local-chat", state_dir=None, json=True)
            run_test(args)
        checks_by_name = {c["check"]: c for c in captured[0]["checks"]}
        # dry_run_apply check should be present
        self.assertIn("dry_run_apply", checks_by_name)

    def test_run_test_dry_run_detail_is_honest_on_error(self):
        """Regression: when a service errors in dry-run, the detail must name the
        service and its reason — not claim 'N service(s) would start'. Found by
        running the real CLI (recipe test local-chat) where the llm service errors
        because ollama is absent, yet the old detail still said both would start."""
        from aictl.cmd.recipe import run_test

        good = MagicMock(name="webui", status="dry-run", error="")
        good.name = "webui"
        bad = MagicMock(status="error", error="Cannot determine how to start this service")
        bad.name = "llm"

        captured = []
        with patch("aictl.cmd.recipe.get_recipe") as mock_recipe, \
             patch("aictl.cmd.recipe.apply_stack", return_value=[good, bad]), \
             patch("aictl.cmd.recipe.validate_manifest", return_value=[]), \
             patch("aictl.cmd.recipe.print_json", side_effect=captured.append):
            manifest = MagicMock()
            manifest.services = []  # no GPU-required services
            mock_recipe.return_value = manifest
            args = argparse.Namespace(name="local-chat", state_dir=None, json=True)
            ret = run_test(args)

        self.assertEqual(ret, 1)  # errored service → overall fail
        dry = next(c for c in captured[0]["checks"] if c["check"] == "dry_run_apply")
        self.assertFalse(dry["passed"])
        # Detail must name the errored service and not claim it would start
        self.assertIn("llm", dry["detail"])
        self.assertIn("Cannot determine", dry["detail"])
        self.assertNotIn("would start", dry["detail"])

    def test_run_test_dry_run_passes_when_all_planned(self):
        """When every service plans cleanly, detail reports the count and passes."""
        from aictl.cmd.recipe import run_test
        s1 = MagicMock(status="dry-run", error="")
        s1.name = "a"
        s2 = MagicMock(status="dry-run", error="")
        s2.name = "b"
        captured = []
        with patch("aictl.cmd.recipe.get_recipe") as mock_recipe, \
             patch("aictl.cmd.recipe.apply_stack", return_value=[s1, s2]), \
             patch("aictl.cmd.recipe.validate_manifest", return_value=[]), \
             patch("aictl.cmd.recipe.print_json", side_effect=captured.append):
            manifest = MagicMock()
            manifest.services = []
            mock_recipe.return_value = manifest
            args = argparse.Namespace(name="x", state_dir=None, json=True)
            ret = run_test(args)
        self.assertEqual(ret, 0)
        dry = next(c for c in captured[0]["checks"] if c["check"] == "dry_run_apply")
        self.assertTrue(dry["passed"])
        self.assertIn("2 service(s) would start", dry["detail"])

    def test_run_export_unknown_recipe(self):
        from aictl.cmd.recipe import run_export
        captured = []
        with patch("aictl.cmd.recipe.print_json", side_effect=captured.append):
            args = argparse.Namespace(name="no-such-recipe", output="", state_dir=None, json=True)
            ret = run_export(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["exported"])

    def test_run_export_known_recipe(self):
        from aictl.cmd.recipe import run_export
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        out = str(tmpdir / "local-chat.json")
        captured = []
        with patch("aictl.cmd.recipe.print_json", side_effect=captured.append):
            args = argparse.Namespace(name="local-chat", output=out, state_dir=None, json=True)
            ret = run_export(args)
        self.assertEqual(ret, 0)
        self.assertTrue(pathlib.Path(out).exists())
        data = json.loads(pathlib.Path(out).read_text())
        self.assertIn("name", data)
        self.assertEqual(data["name"], "local-chat")
        self.assertTrue(captured[0]["exported"])

    def test_run_export_has_services(self):
        from aictl.cmd.recipe import run_export
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        out = str(tmpdir / "recipe.json")
        run_export(argparse.Namespace(name="local-chat", output=out, state_dir=None, json=False))
        data = json.loads(pathlib.Path(out).read_text())
        self.assertIn("services", data)
        self.assertIsInstance(data["services"], list)
        self.assertGreater(len(data["services"]), 0)


# ── bench history ─────────────────────────────────────────────────────────────

class TestBenchHistory(unittest.TestCase):

    def _make_parser(self):
        from aictl.cmd.bench import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_history_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["bench", "history"])
        self.assertEqual(args.func.__name__, "run_history")

    def test_history_last_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["bench", "history", "--last", "50"])
        self.assertEqual(args.last, 50)

    def test_run_history_empty(self):
        from aictl.cmd.bench import run_history
        captured = []
        with patch("aictl.cmd.bench.print_json", side_effect=captured.append), \
             patch("aictl.core.perf.read_recent", return_value=[]):
            args = argparse.Namespace(last=20, json=True)
            ret = run_history(args)
        self.assertEqual(ret, 0)
        # May or may not capture (returns early without print_json on empty)

    def test_run_history_with_records_json(self):
        from aictl.cmd.bench import run_history
        from aictl.core.perf import PerfRecord
        records = [
            PerfRecord(timestamp=time.time() - i * 60,
                       command="bench --mock",
                       duration_ms=float(100 + i * 10),
                       exit_code=0,
                       rss_mb_peak=50.0)
            for i in range(3)
        ]
        captured = []
        with patch("aictl.cmd.bench.print_json", side_effect=captured.append), \
             patch("aictl.core.perf.read_recent", return_value=records):
            args = argparse.Namespace(last=20, json=True)
            ret = run_history(args)
        self.assertEqual(ret, 0)
        self.assertIsInstance(captured[0], list)
        self.assertEqual(len(captured[0]), 3)
        first = captured[0][0]
        self.assertIn("command", first)
        self.assertIn("duration_ms", first)
        self.assertIn("ts", first)

    def test_run_history_text_no_crash(self):
        from aictl.cmd.bench import run_history
        from aictl.core.perf import PerfRecord
        records = [
            PerfRecord(timestamp=time.time(),
                       command="aictl bench --mock",
                       duration_ms=120.0, exit_code=0, rss_mb_peak=45.0),
        ]
        with patch("aictl.core.perf.read_recent", return_value=records):
            args = argparse.Namespace(last=20, json=False)
            ret = run_history(args)
        self.assertEqual(ret, 0)


if __name__ == "__main__":
    unittest.main()
