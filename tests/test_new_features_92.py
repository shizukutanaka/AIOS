"""Pass 92 (loop, Socratic new perspective): input-validation hardening.

New lens: adversarial / nonsensical inputs, not just happy paths and not-found.
Found by fuzzing real invocations:
  - `quota create --tokens-per-month -100` was accepted (rc=0) — a negative budget
    is meaningless and corrupts utilization/enforcement comparisons.
  - `snapshot create --label ../../etc/passwd` let the label flow into the
    snapshot filename → path traversal escaping the snapshot dir.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch


class TestQuotaRejectsNonPositive(unittest.TestCase):

    def _run(self, tokens):
        from aictl.cmd.quota import run_create
        with tempfile.TemporaryDirectory() as sd, \
             patch.dict(os.environ, {"AIOS_STATE_DIR": sd}):
            return run_create(argparse.Namespace(
                team="t", tokens_per_month=tokens, priority="normal", json=False))

    def test_negative_rejected(self):
        self.assertEqual(self._run(-100), 1)

    def test_zero_rejected(self):
        self.assertEqual(self._run(0), 1)

    def test_positive_accepted(self):
        self.assertEqual(self._run(1000), 0)


class TestSnapshotLabelSanitized(unittest.TestCase):

    def _create(self, label):
        from aictl.core.state import StateStore
        from aictl.core.snapshots import SnapshotManager
        sd = tempfile.mkdtemp()
        mgr = SnapshotManager(StateStore(sd))
        snap = mgr.create(label=label)
        return pathlib.Path(sd), mgr.snap_dir, snap

    def test_traversal_label_stays_in_snap_dir(self):
        sd, snap_dir, snap = self._create("../../etc/passwd")
        # snap_id must be a single safe path component (no separators / ..).
        self.assertNotIn("/", snap.snapshot_id)
        self.assertNotIn("..", snap.snapshot_id)
        # The written file must resolve inside snap_dir.
        written = snap_dir / f"{snap.snapshot_id}.json"
        self.assertTrue(written.resolve().is_relative_to(snap_dir.resolve()))
        self.assertTrue(written.exists())
        # No file escaped to the parent of the state dir.
        self.assertFalse(any("passwd" in p.name for p in sd.parent.glob("*passwd*")))

    def test_normal_label_preserved(self):
        _, _, snap = self._create("nightly")
        self.assertTrue(snap.snapshot_id.startswith("nightly_"))

    def test_empty_label_still_valid(self):
        _, snap_dir, snap = self._create("")
        self.assertTrue((snap_dir / f"{snap.snapshot_id}.json").exists())


if __name__ == "__main__":
    unittest.main()
