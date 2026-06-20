"""Pass 115 (loop): identifier hygiene for LoRA adapter names.

`lora add`/`inspect`/`delete`/`activate`/`deactivate`/`route` used the raw
adapter name as the registry key with no trimming or validation — the same
class of bug already fixed for tenant (Pass 114) and quota. Consequences:

  - `lora add "finance "` stored the key "finance " — `lora inspect "finance"`
    then reported "not found" (an adapter you can't reference by its own name).
  - `lora add ""` / `"   "` happily created junk empty/whitespace adapters
    (and `add` with an empty --base silently registered an orphan).

All adapter operations now strip the name, reject empty/whitespace-only on
create, and use the normalized name for both storage and lookup so that
add/inspect/delete/activate are symmetric ("finance " and "finance" refer to
the same adapter).
"""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _add(name, base="llama3.1:8b", path="", rank=16):
    return argparse.Namespace(name=name, base=base, path=path, rank=rank, json=False)


def _name(name):
    return argparse.Namespace(name=name, json=False)


class TestLoraNameHygiene(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Pin LoRAManager's state dir so each test is isolated.
        from aictl.runtime.lora import LoRAManager
        self._patch = patch("aictl.cmd.lora.LoRAManager",
                            lambda *a, **k: LoRAManager(Path(self.tmp)))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_padded_add_is_findable_by_trimmed(self):
        from aictl.cmd.lora import run_add, run_inspect
        self.assertEqual(run_add(_add("finance ")), 0)
        self.assertEqual(run_inspect(_name("finance")), 0)   # was: 1 (not found)

    def test_empty_and_whitespace_name_rejected(self):
        from aictl.cmd.lora import run_add
        self.assertEqual(run_add(_add("")), 1)
        self.assertEqual(run_add(_add("   ")), 1)

    def test_empty_base_rejected(self):
        from aictl.cmd.lora import run_add
        self.assertEqual(run_add(_add("finance", base="  ")), 1)

    def test_delete_symmetric_with_padding(self):
        from aictl.cmd.lora import run_add, run_delete
        self.assertEqual(run_add(_add("finance")), 0)
        self.assertEqual(run_delete(_name("  finance  ")), 0)   # trimmed match

    def test_activate_deactivate_symmetric_with_padding(self):
        from aictl.cmd.lora import run_add, run_activate, run_deactivate
        self.assertEqual(run_add(_add("legal")), 0)
        self.assertEqual(run_deactivate(_name(" legal ")), 0)
        self.assertEqual(run_activate(_name("legal")), 0)

    def test_route_symmetric_with_padding(self):
        from aictl.cmd.lora import run_add, run_route
        self.assertEqual(run_add(_add("medical")), 0)
        ra = argparse.Namespace(name=" medical ", weight=50, json=False)
        self.assertEqual(run_route(ra), 0)                      # was: "not found"


if __name__ == "__main__":
    unittest.main()
