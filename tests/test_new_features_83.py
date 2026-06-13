"""Pass 83 (Socratic round 3) regression tests.

The global `--state-dir` flag delivers a *string*, but StateStore assumed a Path
and crashed on `str.mkdir` — breaking ~24 commands (snapshot/model/ps/warmup/
status/...) under the documented `aictl --state-dir DIR <cmd>` form. Mocked tests
missed it because they passed pathlib.Path objects; the real CLI passes a str.

Found by exercising the export/import round-trip through the real CLI.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


class TestStateStoreAcceptsStr(unittest.TestCase):
    """StateStore must accept a str path (as the global --state-dir delivers)."""

    def test_str_path_does_not_crash(self):
        from aictl.core.state import StateStore
        with tempfile.TemporaryDirectory() as td:
            sd = str(pathlib.Path(td) / "sub")  # plain string, non-existent yet
            store = StateStore(sd)  # used to raise AttributeError: str.mkdir
            self.assertTrue(store.dir.exists())
            self.assertIsInstance(store.dir, pathlib.Path)

    def test_path_object_still_works(self):
        from aictl.core.state import StateStore
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(pathlib.Path(td))
            self.assertTrue(store.dir.exists())
            self.assertIsInstance(store.dir, pathlib.Path)

    def test_none_uses_default(self):
        from aictl.core.state import StateStore, DEFAULT_STATE_DIR
        store = StateStore(None)
        self.assertEqual(store.dir, DEFAULT_STATE_DIR)


class TestGlobalStateDirEndToEnd(unittest.TestCase):
    """`aictl --state-dir DIR <cmd>` (string from argv) must not crash."""

    def _run_main(self, argv: list[str]) -> tuple[int, str]:
        from aictl.__main__ import main
        buf = io.StringIO()
        # Isolate perf/state via env so the run is deterministic & non-polluting.
        with tempfile.TemporaryDirectory() as envdir, \
             patch.dict(os.environ, {"AIOS_STATE_DIR": envdir}), \
             patch.object(sys, "argv", argv), redirect_stdout(buf):
            rc = main()
        return rc, buf.getvalue()

    def test_snapshot_list_with_global_state_dir(self):
        with tempfile.TemporaryDirectory() as sd:
            rc, out = self._run_main(["aictl", "--json", "--state-dir", sd,
                                      "snapshot", "list"])
        self.assertEqual(rc, 0)
        self.assertIsInstance(json.loads(out), list)

    def test_model_list_with_global_state_dir(self):
        with tempfile.TemporaryDirectory() as sd:
            rc, out = self._run_main(["aictl", "--json", "--state-dir", sd,
                                      "model", "list"])
        self.assertEqual(rc, 0)
        json.loads(out)

    def test_status_with_global_state_dir(self):
        with tempfile.TemporaryDirectory() as sd:
            rc, _ = self._run_main(["aictl", "--state-dir", sd, "status"])
        self.assertEqual(rc, 0)


class TestSnapshotRoundTripEndToEnd(unittest.TestCase):
    """create -> export -> import(fresh) -> list must round-trip via real handlers."""

    def test_snapshot_export_import_round_trip(self):
        from aictl.cmd.snapshot import run_create, run_export, run_import, run_list
        import argparse
        with tempfile.TemporaryDirectory() as s1, \
             tempfile.TemporaryDirectory() as s2, \
             tempfile.TemporaryDirectory() as w:
            # create in s1 (string state_dir, as the CLI delivers)
            run_create(argparse.Namespace(state_dir=s1, label="rt", json=False))
            # find its id
            captured = []
            with patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
                run_list(argparse.Namespace(state_dir=s1, json=True))
            snaps = captured[0]
            self.assertEqual(len(snaps), 1)
            sid = snaps[0]["id"]
            # export
            out = str(pathlib.Path(w) / "snap.json")
            rc = run_export(argparse.Namespace(state_dir=s1, id=sid, output=out, json=False))
            self.assertEqual(rc, 0)
            self.assertTrue(pathlib.Path(out).exists())
            # import into fresh s2
            rc = run_import(argparse.Namespace(state_dir=s2, file=out, restore=False, json=False))
            self.assertEqual(rc, 0)
            # list in s2 → exactly one
            captured2 = []
            with patch("aictl.cmd.snapshot.print_json", side_effect=captured2.append):
                run_list(argparse.Namespace(state_dir=s2, json=True))
            self.assertEqual(len(captured2[0]), 1)
            self.assertEqual(captured2[0][0]["id"], sid)


if __name__ == "__main__":
    unittest.main()
