"""Pass 105 (loop): snapshot import rejects path-traversal snapshot_id.

Extends the pass-92 path-traversal fix (which covered snapshot *create* labels) to
the *import* path. run_import wrote a file named from the imported file's
snapshot_id (attacker-controlled), so a crafted export with
snapshot_id="../../etc/evil" would escape the snapshot directory. Now rejected
unless it's a single safe path component.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path


class TestSnapshotImportTraversal(unittest.TestCase):

    def _import(self, payload, state_dir):
        from aictl.cmd.snapshot import run_import
        f = Path(tempfile.mkdtemp()) / "snap.json"
        f.write_text(json.dumps(payload))
        return run_import(argparse.Namespace(file=str(f), state_dir=str(state_dir),
                                             restore=False, json=False))

    def test_traversal_snapshot_id_rejected(self):
        with tempfile.TemporaryDirectory() as sd:
            outside = Path(sd).parent / "ESCAPED_SNAP.json"
            rc = self._import({
                "snapshot_id": f"../../{outside.stem}",
                "created_at": 1.0, "version": "1.6.0", "stacks": [], "models": [],
            }, sd)
            self.assertEqual(rc, 1)
            self.assertFalse(outside.exists())  # nothing escaped

    def test_separator_rejected(self):
        with tempfile.TemporaryDirectory() as sd:
            self.assertEqual(self._import({"snapshot_id": "a/b", "version": "1.6.0",
                                           "created_at": 1.0, "stacks": [], "models": []}, sd), 1)

    def test_clean_id_imports(self):
        with tempfile.TemporaryDirectory() as sd:
            rc = self._import({
                "snapshot_id": "good_snap_123",
                "created_at": 1.0, "version": "1.6.0", "stacks": [], "models": [],
            }, sd)
            self.assertEqual(rc, 0)
            self.assertTrue((Path(sd) / "snapshots" / "good_snap_123.json").exists())


if __name__ == "__main__":
    unittest.main()
