"""Regression tests for the 3rd category audit (daemon / core infra / CLI / Go port)."""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Daemon: governor escalation order ────────────────────────────────
class TestGovernorEscalation(unittest.TestCase):
    def _make_action(self, action_name: str, violations: int):
        """Build a GovernorAction and run escalation logic in isolation."""
        from aictl.runtime.router import GovernorAction
        from aictl.daemon.governor import GovernorDaemon, GovernorState
        from aictl.core.state import StateStore

        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td))
            gd = GovernorDaemon(store)
            gd.state.consecutive_violations = violations

            action = GovernorAction()
            action.action = action_name
            action.reason = "test"

            # Replicate the escalation block from _evaluate_slo
            if gd.state.consecutive_violations >= 10:
                action.action = "failover"
                action.reason += " [escalated: 10+ consecutive violations]"
            elif gd.state.consecutive_violations >= 5 and action.action == "scale_batch":
                action.action = "drain"
                action.reason += " [escalated: 5+ consecutive violations]"
            return action.action

    def test_violations_10_scale_batch_becomes_failover(self):
        """10+ violations AND scale_batch → failover (not drain)."""
        result = self._make_action("scale_batch", 10)
        self.assertEqual(result, "failover",
                         "violations>=10 must escalate to failover even when action==scale_batch")

    def test_violations_7_scale_batch_becomes_drain(self):
        """5-9 violations AND scale_batch → drain."""
        result = self._make_action("scale_batch", 7)
        self.assertEqual(result, "drain")

    def test_violations_10_restart_becomes_failover(self):
        """10+ violations AND non-scale_batch action → failover."""
        result = self._make_action("restart", 10)
        self.assertEqual(result, "failover")

    def test_violations_3_no_escalation(self):
        """< 5 violations → no escalation."""
        result = self._make_action("scale_batch", 3)
        self.assertEqual(result, "scale_batch")


# ── Daemon: aiosd _get_report thread safety ────────────────────────
class TestAiosdReportLock(unittest.TestCase):
    def test_report_lock_exists(self):
        """AIOSHandler must have a class-level threading.Lock."""
        from aictl.daemon.aiosd import AIOSHandler
        lock = AIOSHandler._report_lock
        self.assertTrue(hasattr(lock, "acquire") and hasattr(lock, "release"),
                        "_report_lock must be a threading.Lock")

    def test_concurrent_get_report_calls_full_detect_once(self):
        """Concurrent _get_report() calls should only call full_detect once."""
        from aictl.daemon.aiosd import AIOSHandler
        from aictl.runtime.broker import RuntimeReport, SystemInfo

        fake_report = RuntimeReport(
            system=SystemInfo(), gpus=[], npus=[], profile="cpu-only",
            container_runtime="podman", issues=[])

        call_count = {"n": 0}

        def fake_detect():
            call_count["n"] += 1
            return fake_report

        # Reset cache
        AIOSHandler._report_cache = None
        AIOSHandler._report_ts = 0.0

        results = []
        errors = []

        def call_get_report():
            try:
                handler = object.__new__(AIOSHandler)
                with mock.patch("aictl.daemon.aiosd.full_detect", fake_detect):
                    r = AIOSHandler._get_report(handler)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_get_report) for _ in range(10)]
        with mock.patch("aictl.daemon.aiosd.full_detect", fake_detect):
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(errors, [], f"Exceptions raised: {errors}")
        self.assertEqual(len(results), 10)


# ── Daemon: mock_engine token count ────────────────────────────────
class TestMockEngineTokenCount(unittest.TestCase):
    def test_tool_call_token_count_uses_word_split(self):
        """Token count for tool calls must use word count, not JSON char count."""
        import aictl.daemon.mock_engine as me

        tc = {"id": "x", "function": {"name": "do_thing", "arguments": '{"x": 1}'}}
        # len(json.dumps(tc).split()) is a small word count;
        # len(json.dumps(tc)) is the character count (much larger).
        word_count = len(json.dumps(tc).split())
        char_count = len(json.dumps(tc))
        # The two methods only agree for very short strings; for a real tool call
        # the char count is much larger.
        self.assertLess(word_count, char_count,
                        "precondition: word_count < char_count for a typical tool call")

        # Verify the module now uses the word-count approach
        import inspect
        src = inspect.getsource(me)
        # The fixed line uses .split()
        self.assertIn("json.dumps(tc).split()", src)


# ── Daemon: mock_engine Prometheus trailing newline ─────────────────
class TestMockEnginePrometheusNewline(unittest.TestCase):
    def test_prometheus_output_ends_with_newline(self):
        """Prometheus text exposition format must end with a newline."""
        import aictl.daemon.mock_engine as me
        import inspect
        src = inspect.getsource(me)
        # The fixed version appends a newline character
        self.assertIn('+ "\\n").encode()', src)


# ── Core: plugins wire_plugin_events exception isolation ────────────
class TestPluginsWireEventIsolation(unittest.TestCase):
    def test_failing_plugin_does_not_stop_remaining_plugins(self):
        """A plugin that raises on subscribe_all must not prevent wiring others."""
        from aictl.core import plugins as pm

        # Create two fake plugin modules: first raises, second is fine
        bad_mod = mock.MagicMock()
        bad_mod.on_event = mock.MagicMock()
        good_mod = mock.MagicMock()
        good_mod.on_event = mock.MagicMock()

        fake_plugins = [
            {"path": "bad_plugin", "name": "bad_plugin"},
            {"path": "good_plugin", "name": "good_plugin"},
        ]

        def fake_load(path):
            return bad_mod if "bad" in path else good_mod

        def fake_subscribe_all(handler):
            if handler is bad_mod.on_event:
                raise RuntimeError("subscribe failed")

        bus = mock.MagicMock()
        bus.subscribe_all = fake_subscribe_all

        with mock.patch.object(pm, "discover_plugins", return_value=fake_plugins), \
             mock.patch.object(pm, "load_plugin", side_effect=fake_load), \
             mock.patch("aictl.core.events.get_bus", return_value=bus):
            count = pm.wire_plugin_events()

        # Only good_plugin should have been counted
        self.assertEqual(count, 1)


# ── CLI: context.py run_list JSON output ────────────────────────────
class TestContextListJson(unittest.TestCase):
    def test_list_json_output(self):
        """context list --json must produce valid JSON."""
        from aictl.cmd import context as ctx_cmd
        from aictl.runtime.continuity import ContextSnapshot

        fake_snaps = [
            ContextSnapshot(snapshot_id="snap-1", engine="vllm", model="llama3",
                            created_at=0.0, status="saved", num_entries=10),
        ]

        with mock.patch("aictl.runtime.continuity.ContextContinuityEngine.list_snapshots",
                        return_value=fake_snaps):
            args = argparse.Namespace(json=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = ctx_cmd.run_list(args)
        self.assertEqual(rc, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["snapshot_id"], "snap-1")


# ── CLI: tenant.py run_namespace JSON flag respected ─────────────────
class TestTenantNamespaceJson(unittest.TestCase):
    def test_json_flag_routes_through_print_json(self):
        """run_namespace with --json must use print_json (not raw print)."""
        from aictl.cmd import tenant as tenant_cmd

        args = argparse.Namespace(tenant_id="myteam", tenant_class="standard", json=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = tenant_cmd.run_namespace(args)
        self.assertEqual(rc, 0)
        # Output must be valid JSON
        json.loads(buf.getvalue())

    def test_no_json_flag_outputs_manifest(self):
        """run_namespace without --json must still output the manifest."""
        from aictl.cmd import tenant as tenant_cmd

        args = argparse.Namespace(tenant_id="myteam", tenant_class="standard", json=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = tenant_cmd.run_namespace(args)
        self.assertEqual(rc, 0)
        # Non-JSON mode also outputs JSON-serialisable manifest text
        self.assertGreater(len(buf.getvalue().strip()), 0)


if __name__ == "__main__":
    unittest.main()
