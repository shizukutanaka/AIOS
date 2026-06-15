"""Pass 99 (loop, Socratic new perspective): secret handling — no raw API keys.

New lens: does the product protect secrets? The apikey store is well-designed
(SHA-256 hash, raw key never persisted, list masks, shown once). But the proxy
attributed token usage with the *raw* API key as the metering entity_id, so the
secret was persisted in plaintext in the metering store and shown by
`meter report`. Attribution now uses the key's id (SHA-256 prefix) — non-secret,
and equal to the id `apikey list` shows.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestKeyIdNonSecret(unittest.TestCase):

    def test_key_id_does_not_contain_raw_key(self):
        from aictl.core.apikeys import key_id_for
        raw = "aios-SUPERSECRET-abcdef123456"
        kid = key_id_for(raw)
        self.assertNotIn(kid, raw)         # not a substring of the secret
        self.assertNotIn("SUPERSECRET", kid)
        self.assertEqual(len(kid), 8)

    def test_key_id_is_deterministic(self):
        from aictl.core.apikeys import key_id_for
        self.assertEqual(key_id_for("aios-x"), key_id_for("aios-x"))

    def test_key_id_matches_generated_key(self):
        # The metering attribution id must equal the id apikey list shows.
        from aictl.core.apikeys import KeyManager, key_id_for
        with tempfile.TemporaryDirectory() as d:
            mgr = KeyManager(Path(d))
            raw, rec = mgr.generate_key("svc")
            self.assertEqual(rec.key_id, key_id_for(raw))


class TestMeteringDoesNotPersistRawKey(unittest.TestCase):

    def test_attribution_id_is_not_the_raw_key(self):
        # Reproduce the proxy's attribution + a meter record; the raw key must not
        # appear anywhere in the metering store on disk.
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
                from aictl.core.apikeys import key_id_for
                from aictl.core.metering import TokenMeter
                raw = "aios-LEAKME-9988776655"
                entity_id = key_id_for(raw)  # what proxy.py now does
                self.assertNotEqual(entity_id, raw)
                TokenMeter().record(entity_id, "m", 3, 2)
                # No file under the state dir may contain the raw key.
                for f in Path(d).rglob("*"):
                    if f.is_file():
                        self.assertNotIn("aios-LEAKME-9988776655", f.read_text(errors="ignore"),
                                         f"raw key leaked into {f}")


if __name__ == "__main__":
    unittest.main()
