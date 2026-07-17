"""Pass 94 (loop, Socratic new perspective): crash-safe state persistence.

New lens: durability, not just correct inputs/outputs. Every persistent state
writer used naive ``path.write_text(json.dumps(...))``, which truncates the file
before writing — so a crash / full disk / interrupt mid-write leaves a truncated,
corrupt state file and loses the user's config / tenants / quotas / node state.

atomic_write_text writes to a temp file in the same dir, fsyncs, then os.replace.
On failure the original is left intact and the temp is cleaned up.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestAtomicWrite(unittest.TestCase):

    def test_writes_content(self):
        from aictl.core.atomicio import atomic_write_text
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.json"
            atomic_write_text(f, '{"a":1}')
            self.assertEqual(f.read_text(), '{"a":1}')

    def test_creates_parent_dir(self):
        from aictl.core.atomicio import atomic_write_text
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "sub" / "deep" / "x.json"
            atomic_write_text(f, "hi")
            self.assertTrue(f.exists())

    def test_crash_leaves_original_intact(self):
        from aictl.core.atomicio import atomic_write_text
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "state.json"
            atomic_write_text(f, '{"v":1}')
            # Simulate a crash at the atomic-replace step.
            with patch("aictl.core.atomicio.os.replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    atomic_write_text(f, '{"v":2,"big":"' + "x" * 5000 + '"}')
            # Original must be untouched, not truncated.
            self.assertEqual(f.read_text(), '{"v":1}')

    def test_no_temp_files_left_behind(self):
        from aictl.core.atomicio import atomic_write_text
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "state.json"
            atomic_write_text(f, "ok")
            with patch("aictl.core.atomicio.os.replace", side_effect=OSError("boom")):
                try:
                    atomic_write_text(f, "new")
                except OSError:
                    pass
            leftovers = [p.name for p in Path(d).iterdir() if p.name != "state.json"]
            self.assertEqual(leftovers, [])


class TestPersistentWritersAreAtomic(unittest.TestCase):
    """The key state writers must route through atomic_write_text."""

    def test_config_save_uses_atomic(self):
        from aictl.core import config as cfgmod
        with tempfile.TemporaryDirectory() as d, \
             patch("aictl.core.config.atomic_write_text") as mock_aw:
            cfgmod.save_config(cfgmod.Config(), Path(d))
            mock_aw.assert_called_once()

    def test_state_save_node_uses_atomic(self):
        from aictl.core.state import StateStore, NodeState
        with tempfile.TemporaryDirectory() as d, \
             patch("aictl.core.state.atomic_write_text") as mock_aw:
            StateStore(d).save_node(NodeState())
            mock_aw.assert_called_once()

    def test_quota_roundtrip_persists(self):
        # End-to-end: a quota survives a save/load cycle through atomic writes.
        import argparse
        from aictl.cmd.quota import run_create, _load
        with tempfile.TemporaryDirectory() as d, \
             patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
            run_create(argparse.Namespace(team="t", tokens_per_month=999,
                                          priority="normal", json=False))
            self.assertEqual(_load()["teams"]["t"]["tokens_per_month"], 999)


if __name__ == "__main__":
    unittest.main()
