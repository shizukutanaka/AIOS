"""Pass 104 (loop): config import validates before persisting.

New lens: trusting external input on import. config import built a Config from the
file and saved it unconditionally, so importing a config with an out-of-range
daemon port, an unknown trust_policy, a bad log_level, or a non-URL engine
endpoint silently persisted a broken config that would misbehave on the next run
(daemon / trust enforcement). It now runs the existing _validate_config and
rejects an invalid file (rc=1) without writing anything.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _import(payload, state_dir, as_json=False):
    from aictl.cmd.config import run_import
    f = Path(tempfile.mkdtemp()) / "cfg.json"
    f.write_text(json.dumps(payload))
    args = argparse.Namespace(file=str(f), state_dir=str(state_dir), json=as_json)
    return run_import(args)


class TestConfigImportValidates(unittest.TestCase):

    def test_invalid_config_rejected_and_not_persisted(self):
        from aictl.cmd.config import load_config
        with tempfile.TemporaryDirectory() as sd:
            rc = _import({
                "trust_policy": "garbage-policy",
                "daemon": {"port": 99999999},
                "log_level": "screaming",
                "engines": {"vllm": "not-a-url"},
            }, sd)
            self.assertEqual(rc, 1)
            # Nothing persisted — defaults remain.
            cfg = load_config(Path(sd))
            self.assertEqual(cfg.trust_policy, "warn")
            self.assertNotEqual(cfg.daemon.port, 99999999)

    def test_invalid_config_json_lists_problems(self):
        captured = []
        with tempfile.TemporaryDirectory() as sd, \
             patch("aictl.cmd.config.print_json", side_effect=captured.append):
            rc = _import({"trust_policy": "nope"}, sd, as_json=True)
        self.assertEqual(rc, 1)
        self.assertFalse(captured[0]["imported"])
        self.assertTrue(captured[0]["problems"])

    def test_valid_config_imports(self):
        from aictl.cmd.config import load_config
        with tempfile.TemporaryDirectory() as sd:
            rc = _import({
                "trust_policy": "enforce",
                "daemon": {"port": 8123},
                "log_level": "debug",
                "engines": {"vllm": "http://localhost:8000"},
            }, sd)
            self.assertEqual(rc, 0)
            cfg = load_config(Path(sd))
            self.assertEqual(cfg.trust_policy, "enforce")
            self.assertEqual(cfg.daemon.port, 8123)


if __name__ == "__main__":
    unittest.main()
