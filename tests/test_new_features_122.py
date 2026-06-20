"""Pass 122 (loop): query-side identifier hygiene — filters must match trimmed keys.

Passes 115/120 made `lora add` and `meter quota` store identifiers *trimmed*.
But the read side still compared raw, so a padded query silently matched
nothing — an entity you provisioned was invisible to its own (padded) name:

  - `meter usage --entity " eng "`  → 0 rows (stored key is "eng")
  - `lora list --base " llama3:8b "` → 0 rows
  - `lora budget " llama3:8b "` / `lora auto-tune " llama3:8b "` → empty/no match

All read/lookup paths now strip the identifier before comparing, restoring
create→query symmetry across the whole entity surface (cf. tenant/quota inspect
in Pass 114, which already stripped).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class TestMeterUsageFilterHygiene(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = patch.dict(os.environ, {"AIOS_STATE_DIR": self.tmp})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_padded_entity_filter_matches_trimmed_key(self):
        from aictl.cmd.meter import run_quota, run_usage
        # Seed a bucket via set_quota (stores under trimmed "eng").
        run_quota(argparse.Namespace(entity="eng", per_day=100, per_month=None,
                                     json=False))
        # A padded filter must still find it.
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_usage(argparse.Namespace(entity="  eng  ", json=True))
        self.assertEqual(rc, 0)
        rows = json.loads(buf.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entity_id"], "eng")


class TestLoraBaseFilterHygiene(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        from aictl.runtime.lora import LoRAManager
        self._patch = patch("aictl.cmd.lora.LoRAManager",
                            lambda *a, **k: LoRAManager(Path(self.tmp)))
        self._patch.start()
        from aictl.cmd.lora import run_add
        run_add(argparse.Namespace(name="fin", base="llama3:8b",
                                   path="/tmp/x", rank=16, json=False))

    def tearDown(self):
        self._patch.stop()

    def _json(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fn(args)
        return rc, buf.getvalue()

    def test_list_padded_base_matches(self):
        from aictl.cmd.lora import run_list
        rc, out = self._json(run_list, argparse.Namespace(base="  llama3:8b  ",
                                                          json=True))
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)), 1)

    def test_autotune_padded_base_matches(self):
        from aictl.cmd.lora import run_autotune
        rc, out = self._json(run_autotune,
                             argparse.Namespace(base=" llama3:8b ", vram=24,
                                                json=True))
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["base_model"], "llama3:8b")
        self.assertEqual(d["keep"], ["fin"])

    def test_budget_padded_base_matches(self):
        from aictl.cmd.lora import run_budget
        rc, out = self._json(run_budget, argparse.Namespace(base=" llama3:8b ",
                                                           json=True))
        self.assertEqual(rc, 0)
        # The adapter's overhead is counted → non-zero adapter VRAM.
        self.assertGreater(json.loads(out)["adapter_vram_mb"], 0)


if __name__ == "__main__":
    unittest.main()
