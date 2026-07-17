"""Pass 86 (loop): --json error paths emit a parseable JSON object.

The codebase's dominant convention is to emit a JSON object (with found/valid/
deleted/error fields) on --json error paths — model inspect, recipe export,
config get, snapshot restore, tenant inspect already did. Five outliers instead
left stdout empty (human error on stderr), so `aictl --json <cmd> | jq` got
nothing on the not-found path. Aligned to the majority convention.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from unittest.mock import patch


class TestErrorPathsEmitJson(unittest.TestCase):

    def test_snapshot_validate_not_found(self):
        from aictl.cmd.snapshot import run_validate
        with tempfile.TemporaryDirectory() as sd:
            captured = []
            with patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
                rc = run_validate(argparse.Namespace(id="ghost", state_dir=sd, json=True))
        self.assertEqual(rc, 1)
        self.assertFalse(captured[0]["valid"])
        self.assertIn("not found", captured[0]["error"].lower())

    def test_snapshot_delete_not_found(self):
        from aictl.cmd.snapshot import run_delete
        with tempfile.TemporaryDirectory() as sd:
            captured = []
            with patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
                rc = run_delete(argparse.Namespace(id="ghost", state_dir=sd, json=True))
        self.assertEqual(rc, 1)
        self.assertFalse(captured[0]["deleted"])

    def test_snapshot_export_not_found(self):
        from aictl.cmd.snapshot import run_export
        with tempfile.TemporaryDirectory() as sd:
            captured = []
            with patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
                rc = run_export(argparse.Namespace(id="ghost", output="", state_dir=sd, json=True))
        self.assertEqual(rc, 1)
        self.assertFalse(captured[0]["exported"])

    def test_context_switch_not_found(self):
        from aictl.cmd.context import run_switch
        captured = []
        with patch("aictl.cmd.context.ContextContinuityEngine") as MockEng, \
             patch("aictl.cmd.context.print_json", side_effect=captured.append):
            MockEng.return_value.list_snapshots.return_value = []
            rc = run_switch(argparse.Namespace(snapshot_id="ghost", state_dir=None, json=True))
        self.assertEqual(rc, 1)
        self.assertFalse(captured[0]["switched"])

    def test_context_export_not_found(self):
        from aictl.cmd.context import run_export
        captured = []
        with patch("aictl.cmd.context.ContextContinuityEngine") as MockEng, \
             patch("aictl.cmd.context.print_json", side_effect=captured.append):
            MockEng.return_value.list_snapshots.return_value = []
            rc = run_export(argparse.Namespace(snapshot_id="ghost", output="",
                                               state_dir=None, json=True))
        self.assertEqual(rc, 1)
        self.assertFalse(captured[0]["exported"])

    def test_tenant_delete_not_found(self):
        from aictl.cmd.tenant import run_delete
        with tempfile.TemporaryDirectory() as sd:
            captured = []
            with patch("aictl.cmd.tenant.print_json", side_effect=captured.append):
                rc = run_delete(argparse.Namespace(tenant_id="ghost", state_dir=sd, json=True))
        self.assertEqual(rc, 1)
        self.assertFalse(captured[0]["deleted"])

    def test_tenant_delete_success_json(self):
        """Successful delete also emits JSON under --json (not human text)."""
        from aictl.cmd.tenant import run_create, run_delete
        with tempfile.TemporaryDirectory() as sd:
            run_create(argparse.Namespace(tenant_id="acme", tenant_class="standard",
                                          state_dir=sd, json=False))
            captured = []
            with patch("aictl.cmd.tenant.print_json", side_effect=captured.append):
                rc = run_delete(argparse.Namespace(tenant_id="acme", state_dir=sd, json=True))
        self.assertEqual(rc, 0)
        self.assertTrue(captured[0]["deleted"])


if __name__ == "__main__":
    unittest.main()
