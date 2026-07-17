"""Pass 102 (loop): stack loader is schema-evolution & corruption resilient.

New lens: loader resilience across versions. load_stacks was the one dataclass
loader using raw StackEntry(**d) (all others filter unknown keys). So a stacks.json
entry with a field from a newer aictl version raised TypeError, and the surrounding
catch returned [] — every stack lost. Now it filters unknown keys (forward-compat)
and skips an individual bad entry instead of dropping the whole list.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class TestLoadStacksResilient(unittest.TestCase):

    def _store_with(self, rows):
        from aictl.core.state import StateStore
        store = StateStore(Path(tempfile.mkdtemp()))
        store._stacks_path.write_text(json.dumps(rows))
        return store

    def test_unknown_field_from_newer_version_still_loads(self):
        store = self._store_with([
            {"name": "web", "file": "f", "applied_at": 1.0, "status": "running",
             "services": [], "field_added_in_a_future_version": {"x": 1}},
        ])
        names = [e.name for e in store.load_stacks()]
        self.assertEqual(names, ["web"])  # pre-fix: [] (all lost)

    def test_one_bad_entry_does_not_drop_the_rest(self):
        store = self._store_with([
            {"name": "a", "file": "f"},
            "garbage-not-a-dict",
            {"name": "b", "file": "f", "unknown": 1},
        ])
        names = sorted(e.name for e in store.load_stacks())
        self.assertEqual(names, ["a", "b"])

    def test_missing_optional_field_uses_default(self):
        # Old file written before `status`/`services` existed → defaults apply.
        store = self._store_with([{"name": "old", "file": "f"}])
        stacks = store.load_stacks()
        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0].status, "pending")
        self.assertEqual(stacks[0].services, [])

    def test_corrupt_json_returns_empty(self):
        from aictl.core.state import StateStore
        store = StateStore(Path(tempfile.mkdtemp()))
        store._stacks_path.write_text("{ this is not json")
        self.assertEqual(store.load_stacks(), [])


if __name__ == "__main__":
    unittest.main()
