"""Pass 154: batch/prompt/quota stores must validate a dict root (not crash).

Research-informed (調査: Qiita/Zenn — the idiom for loading JSON config in Python
is to `isinstance(data, dict)`-check after `json.loads` and fall back to a default
on a non-object root, since the project forbids the external `jsonschema` dep).

The `_load` helpers in batch.py, prompt.py and quota.py caught malformed JSON but
returned the parsed value WITHOUT checking its type. A list- or scalar-rooted
state file (`[1,2,3]`, `42`) therefore parsed cleanly and was returned as-is, and
the next `db["jobs"]` / `db[name]` / `db["teams"]` access raised — surfaced to the
user as "report a bug" for a corrupt local state file. Same class as the eval
fix (Pass 153), now applied to the persisted-store loaders.

Fix: each `_load` returns the parsed value only when `isinstance(data, dict)`,
otherwise degrades to its default empty structure (matching the existing
best-effort-on-corruption behavior).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch


def _seed(module_name, content):
    """Write `content` to the module's db path under a temp state dir, return loader."""
    d = tempfile.mkdtemp()
    with patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
        mod = __import__(f"aictl.cmd.{module_name}", fromlist=["_db_path", "_load"])
        p = mod._db_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return mod._load()


class TestStoresRejectNonDictRoot(unittest.TestCase):
    def test_batch_list_root_degrades(self):
        db = _seed("batch", "[1, 2, 3]")
        self.assertIsInstance(db, dict)
        self.assertIn("jobs", db)          # default structure
        db["jobs"]["x"] = 1                # the access that used to crash

    def test_batch_scalar_root_degrades(self):
        self.assertIsInstance(_seed("batch", "42"), dict)

    def test_quota_list_root_degrades(self):
        db = _seed("quota", '["a", "b"]')
        self.assertIsInstance(db, dict)
        self.assertIn("teams", db)

    def test_quota_scalar_root_degrades(self):
        self.assertIsInstance(_seed("quota", '"corrupt"'), dict)

    def test_prompt_list_root_degrades(self):
        db = _seed("prompt", '["x"]')
        self.assertIsInstance(db, dict)
        self.assertEqual(db, {})           # prompt's default

    def test_valid_dict_preserved(self):
        db = _seed("batch", json.dumps({"jobs": {"a": {"task": "embed"}},
                                        "updated_at": 1.0}))
        self.assertEqual(db["jobs"]["a"]["task"], "embed")

    def test_malformed_json_still_degrades(self):
        # Pre-existing behavior (parse error) must remain graceful too.
        self.assertIsInstance(_seed("quota", "{not json"), dict)


if __name__ == "__main__":
    unittest.main()
