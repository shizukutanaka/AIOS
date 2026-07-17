"""Pass 156: JSON stores must write atomically (crash-safe saves).

Research-informed (調査: Qiita/Zenn — the crash-safe write idiom is temp file →
flush → os.fsync → os.replace; the project already ships
`aictl.core.atomicio.atomic_write_text`, used by quota/cluster). batch._save,
prompt._save and route._save_config still used a plain `path.write_text`, so a
crash or interrupt mid-write left a TRUNCATED/corrupt store — the very corruption
the Pass-154 isinstance(dict) guards now have to discard. Preventing the
corruption (atomic write) is the complementary fix.

Asserts each _save now routes through atomic_write_text, that the original file
survives a mid-write crash (os.replace is never reached on error), and that a
normal save still round-trips through _load.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestStoresUseAtomicWrite(unittest.TestCase):
    def _save_uses_atomic(self, module_name, save_attr, sample):
        d = tempfile.mkdtemp()
        with patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
            mod = __import__(f"aictl.cmd.{module_name}",
                             fromlist=[save_attr, "atomic_write_text", "_load"])
            with patch.object(mod, "atomic_write_text",
                              wraps=mod.atomic_write_text) as spy:
                getattr(mod, save_attr)(sample)
            spy.assert_called_once()

    def test_batch_save_atomic(self):
        self._save_uses_atomic("batch", "_save", {"jobs": {}})

    def test_prompt_save_atomic(self):
        self._save_uses_atomic("prompt", "_save", {"p": {"versions": []}})

    def test_route_save_config_atomic(self):
        self._save_uses_atomic("route", "_save_config",
                               {"simple": {"model": "m", "max_score": 30}})


class TestAtomicCrashSafety(unittest.TestCase):
    def test_original_survives_mid_write_crash(self):
        # If the write fails partway, the previous good file must remain intact
        # (os.replace is the last step and never runs on error).
        from aictl.core.atomicio import atomic_write_text
        d = Path(tempfile.mkdtemp())
        target = d / "store.json"
        atomic_write_text(target, json.dumps({"v": 1}))

        # Simulate a crash during the temp-file write.
        with patch("os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_write_text(target, json.dumps({"v": 2}))

        # Old content intact, and no .tmp debris left behind.
        self.assertEqual(json.loads(target.read_text()), {"v": 1})
        self.assertEqual(list(d.glob(".*.tmp")), [])


class TestRoundTripStillWorks(unittest.TestCase):
    def test_batch_round_trip(self):
        d = tempfile.mkdtemp()
        with patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
            from aictl.cmd import batch
            batch._save({"jobs": {"j1": {"task": "embed"}}})
            self.assertEqual(batch._load()["jobs"]["j1"]["task"], "embed")

    def test_route_config_round_trip(self):
        d = tempfile.mkdtemp()
        with patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
            from aictl.cmd import route
            route._save_config({"simple": {"model": "custom", "max_score": 30}})
            self.assertEqual(route._load_config()["simple"]["model"], "custom")


if __name__ == "__main__":
    unittest.main()
