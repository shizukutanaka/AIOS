"""Pass 120 (loop): `meter quota` — negative quota, entity hygiene, --json.

`meter quota` passed --per-day/--per-month straight to TokenMeter.set_quota
with no validation, and ignored both entity hygiene and the --json contract.
Three defects in one tiny handler:

  1. NEGATIVE quota silently broken. Quota semantics are "0 = unlimited,
     positive = a cap", and enforcement only fires on `quota > 0`. So
     `meter quota team --per-day -100` stored -100, which enforcement reads as
     unlimited — the user believes they capped usage while no cap ever applies.
  2. Entity not normalized (Pass 114 class): `meter quota "team "` stored the
     padded key, unreachable as "team".
  3. No --json branch (Pass 119 class): always printed the human "Quota set".

The handler now strips/validates the entity, rejects negative quotas (0 stays
valid = unlimited), rejects the no-op case where neither quota is given, and
emits JSON under --json.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


class TestMeterQuota(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = patch.dict(os.environ, {"AIOS_STATE_DIR": self.tmp})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _args(self, entity, per_day=None, per_month=None, json=False):
        return argparse.Namespace(entity=entity, per_day=per_day,
                                  per_month=per_month, json=json)

    def test_negative_per_day_rejected(self):
        from aictl.cmd.meter import run_quota
        self.assertEqual(run_quota(self._args("team", per_day=-100)), 1)

    def test_negative_per_month_rejected(self):
        from aictl.cmd.meter import run_quota
        self.assertEqual(run_quota(self._args("team", per_month=-1)), 1)

    def test_zero_is_valid_unlimited(self):
        from aictl.cmd.meter import run_quota
        self.assertEqual(run_quota(self._args("team", per_day=0)), 0)

    def test_empty_entity_rejected(self):
        from aictl.cmd.meter import run_quota
        self.assertEqual(run_quota(self._args("   ", per_day=100)), 1)

    def test_no_quota_specified_rejected(self):
        from aictl.cmd.meter import run_quota
        self.assertEqual(run_quota(self._args("team")), 1)

    def test_padded_entity_stored_trimmed_and_enforceable(self):
        from aictl.cmd.meter import run_quota
        from aictl.core.metering import TokenMeter
        self.assertEqual(run_quota(self._args(" eng ", per_day=50)), 0)
        # The quota is stored under the trimmed key and is enforceable as "eng".
        bucket = TokenMeter().get_usage("eng")
        self.assertIsNotNone(bucket)
        self.assertEqual(bucket.quota_tokens_per_day, 50)

    def test_json_contract(self):
        from aictl.cmd.meter import run_quota
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_quota(self._args("team", per_month=1000, json=True))
        self.assertEqual(rc, 0)
        d = json.loads(buf.getvalue())
        self.assertEqual(d["entity"], "team")
        self.assertEqual(d["per_month"], 1000)


if __name__ == "__main__":
    unittest.main()
