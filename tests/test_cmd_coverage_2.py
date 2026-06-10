"""Coverage tests for previously-untested cmd modules (batch 2).

Exercises audit, meter, ps, and node command handlers against isolated
temporary state directories, asserting on return codes and --json output.
"""

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _ns(**kw):
    return argparse.Namespace(**kw)


def _capture_json(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(args)
    out = buf.getvalue().strip()
    return rc, (json.loads(out) if out else None)


# ─── audit ────────────────────────────────────────────────

class TestAuditCmd(unittest.TestCase):
    def test_audit_records_and_reads_back(self):
        from aictl.cmd import audit
        from aictl.core.audit import audit as write_audit, get_audit_log
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            # get_audit_log caches a module-level singleton; force it to our dir
            get_audit_log(dp)
            write_audit("test.event", resource="r1", action="create",
                        outcome="success", state_dir=dp)
            rc, entries = _capture_json(
                audit.run, _ns(state_dir=str(dp), lines=20, event="", json=True))
            self.assertEqual(rc, 0)
            self.assertIsInstance(entries, list)
            self.assertTrue(any(e["event"] == "test.event" for e in entries))

    def test_audit_event_filter(self):
        from aictl.cmd import audit
        from aictl.core.audit import audit as write_audit, get_audit_log
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            get_audit_log(dp)
            write_audit("model.registered", resource="m", action="register",
                        outcome="success", state_dir=dp)
            write_audit("trust.violation", resource="m2", action="verify",
                        outcome="failure", state_dir=dp)
            rc, entries = _capture_json(
                audit.run,
                _ns(state_dir=str(dp), lines=20, event="trust.violation", json=True))
            self.assertEqual(rc, 0)
            for e in entries:
                self.assertEqual(e["event"], "trust.violation")


# ─── meter ────────────────────────────────────────────────

class TestMeterCmd(unittest.TestCase):
    def test_usage_empty_returns_0(self):
        from aictl.cmd import meter
        # No usage recorded — handler reads from default TokenMeter; just
        # verify it does not crash and returns 0 with --json.
        rc, _ = _capture_json(meter.run_usage, _ns(entity="", json=True))
        self.assertEqual(rc, 0)

    def test_usage_records_via_meter(self):
        from aictl.cmd import meter
        from aictl.core.metering import TokenMeter
        with tempfile.TemporaryDirectory() as d:
            tm = TokenMeter(Path(d))
            tm.record("team-a", "mock", prompt_tokens=100, completion_tokens=50)
            buckets = tm.list_usage()
            self.assertTrue(any(b.entity_id == "team-a" for b in buckets))
            total = next(b for b in buckets if b.entity_id == "team-a").total_tokens
            self.assertEqual(total, 150)


# ─── ps ───────────────────────────────────────────────────

class TestPsCmd(unittest.TestCase):
    def test_ps_json_no_services(self):
        from aictl.cmd import ps
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                ps.run, _ns(state_dir=Path(d), stack="", json=True))
            self.assertEqual(rc, 0)
            self.assertIsNotNone(parsed)


# ─── node ─────────────────────────────────────────────────

class TestNodeCmd(unittest.TestCase):
    def test_token_generation_json(self):
        from aictl.cmd import node
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                node.run_token, _ns(state_dir=Path(d), json=True))
            self.assertEqual(rc, 0)
            self.assertIn("token", parsed)
            self.assertTrue(len(parsed["token"]) > 0)

    def test_list_empty_cluster_json(self):
        from aictl.cmd import node
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                node.run_list, _ns(state_dir=Path(d), json=True))
            self.assertEqual(rc, 0)
            self.assertIn("mode", parsed)

    def test_status_empty_cluster_json(self):
        from aictl.cmd import node
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                node.run_status, _ns(state_dir=Path(d), json=True))
            # No peers configured → status is purely local, no network calls
            self.assertEqual(rc, 0)
            self.assertIn("mode", parsed)
            self.assertEqual(parsed["peers"], [])


if __name__ == "__main__":
    unittest.main()
