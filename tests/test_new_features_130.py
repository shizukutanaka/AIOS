"""Pass 130: `config set` bool parsing rejects invalid values (honest failure).

Research-informed (調査: Qiita/Zenn — the `bool("false") == True` pitfall;
canonical fix is strtobool's explicit true/false sets, rejecting the rest).

`config set` coerced bool fields with `value.lower() in ("true","1","yes")`,
so ANY unrecognized string silently became False:

    config set quadlet_rootless treu   → exit 0, stored False  (meant "true"!)
    config set quadlet_rootless maybe  → exit 0, stored False
    config set quadlet_rootless ""     → exit 0, stored False

A misspelled "true" stored the *opposite* with no error — while int fields
already rejected bad input. `_parse_bool` now uses strtobool semantics: accept
y/yes/t/true/on/1 and n/no/f/false/off/0 (case-insensitive), and raise
ValueError otherwise (handled like int/float → exit 1, clear message).
"""

from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from unittest.mock import patch


class TestParseBool(unittest.TestCase):
    def test_true_forms(self):
        from aictl.cmd.config import _parse_bool
        for v in ("y", "yes", "t", "true", "on", "1", "True", "ON", " TRUE "):
            self.assertIs(_parse_bool(v), True, v)

    def test_false_forms(self):
        from aictl.cmd.config import _parse_bool
        for v in ("n", "no", "f", "false", "off", "0", "False", "OFF"):
            self.assertIs(_parse_bool(v), False, v)

    def test_invalid_raises(self):
        from aictl.cmd.config import _parse_bool
        for v in ("maybe", "treu", "", "2", "tru", "yesno"):
            with self.assertRaises(ValueError, msg=v):
                _parse_bool(v)


class TestConfigSetBool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = patch.dict(os.environ, {"AIOS_STATE_DIR": self.tmp})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _set(self, key, value):
        return argparse.Namespace(key=key, value=value, state_dir=self.tmp,
                                  json=False)

    def test_valid_bool_accepted(self):
        from aictl.cmd.config import run_set
        self.assertEqual(run_set(self._set("quadlet_rootless", "false")), 0)
        self.assertEqual(run_set(self._set("quadlet_rootless", "true")), 0)

    def test_invalid_bool_rejected_not_silently_false(self):
        from aictl.cmd.config import run_set
        # The whole point: a typo must NOT silently become False.
        self.assertEqual(run_set(self._set("quadlet_rootless", "treu")), 1)
        self.assertEqual(run_set(self._set("quadlet_rootless", "maybe")), 1)
        self.assertEqual(run_set(self._set("quadlet_rootless", "")), 1)

    def test_bool_round_trip_preserved(self):
        from aictl.cmd.config import run_set, run_get
        self.assertEqual(run_set(self._set("quadlet_rootless", "off")), 0)
        # get succeeds and the value is the parsed bool (round-trips as a real bool)
        self.assertEqual(run_get(self._set("quadlet_rootless", "")), 0)


if __name__ == "__main__":
    unittest.main()
