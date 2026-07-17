"""Pass 157: API-keys file must be 0600 (secret) and saved atomically.

Research-informed (調査: Qiita/Zenn — secret files like API/SSH keys must be
0600 owner-only; 0644 is "too open"). `KeyManager._save_keys` used a plain
`write_text`, which leaves the file at the process umask — commonly 0o644
(world-readable). Verified: the generated `api_keys.json` was 0o644, so any local
user could read every key's hash and metadata. The same write was also
non-atomic (V7 save-side), so a crash mid-write could corrupt the auth store.

Fix: `atomic_write_text` gained an optional `mode`; `_save_keys` writes with
`mode=0o600` — atomic AND owner-only. Token buckets (billing state) also moved to
atomic_write_text. An already-world-readable file is corrected to 0o600 on the
next save (os.replace swaps in the new 0o600 inode).
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _km(state_dir):
    from aictl.core.apikeys import KeyManager
    return KeyManager(Path(state_dir))


class TestApiKeysPermissions(unittest.TestCase):
    def test_keys_file_is_0600(self):
        d = tempfile.mkdtemp()
        mgr = _km(d)
        mgr.generate_key("k1")
        mode = stat.S_IMODE(mgr._keys_path.stat().st_mode)
        self.assertEqual(mode, 0o600, f"keys file is {oct(mode)}, expected 0o600")

    def test_world_readable_file_corrected_on_save(self):
        d = tempfile.mkdtemp()
        mgr = _km(d)
        mgr._keys_path.write_text("{}")
        os.chmod(mgr._keys_path, 0o644)
        mgr.generate_key("k2")                       # triggers _save_keys
        self.assertEqual(stat.S_IMODE(mgr._keys_path.stat().st_mode), 0o600)

    def test_key_round_trips_after_atomic_save(self):
        d = tempfile.mkdtemp()
        mgr = _km(d)
        raw, _ = mgr.generate_key("k3")
        ok, _reason, key = mgr.validate(raw)
        self.assertTrue(ok)
        self.assertIsNotNone(key)

    def test_no_tmp_debris(self):
        d = tempfile.mkdtemp()
        mgr = _km(d)
        mgr.generate_key("k4")
        self.assertEqual(list(Path(d).glob(".*.tmp")), [])


class TestAtomicWriteMode(unittest.TestCase):
    def test_mode_applied(self):
        from aictl.core.atomicio import atomic_write_text
        d = Path(tempfile.mkdtemp())
        f = d / "secret.json"
        atomic_write_text(f, '{"a": 1}', mode=0o600)
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)

    def test_default_no_explicit_mode_still_writes(self):
        from aictl.core.atomicio import atomic_write_text
        d = Path(tempfile.mkdtemp())
        f = d / "plain.json"
        atomic_write_text(f, '{"a": 1}')        # mode=None
        self.assertEqual(json.loads(f.read_text()), {"a": 1})


class TestMeteringAtomic(unittest.TestCase):
    def test_save_buckets_atomic_no_debris(self):
        from aictl.core.metering import TokenMeter
        d = Path(tempfile.mkdtemp())
        meter = TokenMeter(d)
        meter._save_buckets({})
        self.assertTrue(meter._buckets_path.exists())
        self.assertEqual(json.loads(meter._buckets_path.read_text()), {})
        self.assertEqual(list(d.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
