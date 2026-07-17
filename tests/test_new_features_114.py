"""Pass 114 (loop): identifier hygiene for tenant and quota keys.

Leading/trailing whitespace is never part of an entity's identity, but
`tenant create` and `quota create` used the raw argument as the registry key
with no trimming or validation. Consequences:

  - `tenant create "team "` stored the key "team " — `tenant inspect "team"`
    then reported "not found" (an entity you can't reference by its own name).
  - `tenant create ""` / `"   "` happily created junk empty/whitespace tenants.
  - quota had the identical bug on the team key.

Both commands now strip the id, reject empty/whitespace-only, and use the
normalized id for both storage and lookup so create/inspect/delete/reset are
symmetric ("team " and "team" refer to the same entity).
"""

from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from unittest.mock import patch


class TestTenantIdHygiene(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = patch.dict(os.environ, {"AIOS_STATE_DIR": self.tmp})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _args(self, tid, **kw):
        base = dict(tenant_id=tid, name="", tenant_class="standard",
                   json=False, state_dir=self.tmp)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_padded_create_is_findable_by_trimmed(self):
        from aictl.cmd.tenant import run_create, run_inspect
        self.assertEqual(run_create(self._args("team ")), 0)
        self.assertEqual(run_inspect(self._args("team")), 0)      # was: 1 (not found)

    def test_empty_and_whitespace_rejected(self):
        from aictl.cmd.tenant import run_create
        self.assertEqual(run_create(self._args("")), 1)
        self.assertEqual(run_create(self._args("   ")), 1)

    def test_delete_is_symmetric_with_padding(self):
        from aictl.cmd.tenant import run_create, run_delete
        self.assertEqual(run_create(self._args("team")), 0)
        self.assertEqual(run_delete(self._args("  team  ")), 0)   # trimmed match


class TestQuotaTeamHygiene(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = patch.dict(os.environ, {"AIOS_STATE_DIR": self.tmp})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _create(self, team, tokens=1000):
        return argparse.Namespace(team=team, tokens_per_month=tokens,
                                  priority="normal", json=False, state_dir=self.tmp)

    def _reset(self, team):
        return argparse.Namespace(team=team, yes=True, json=False, state_dir=self.tmp)

    def test_padded_create_resettable_by_trimmed(self):
        from aictl.cmd.quota import run_create, run_reset
        self.assertEqual(run_create(self._create("eng ")), 0)
        self.assertEqual(run_reset(self._reset("eng")), 0)        # was: "Unknown team"

    def test_empty_team_rejected(self):
        from aictl.cmd.quota import run_create
        self.assertEqual(run_create(self._create("   ")), 1)


if __name__ == "__main__":
    unittest.main()
