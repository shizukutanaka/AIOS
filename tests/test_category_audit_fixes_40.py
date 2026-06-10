"""Pass 40 regression tests: state.py corrupted JSON graceful fallback."""

import json
import tempfile
import unittest
from pathlib import Path


class TestStateCorruptedJsonFallback(unittest.TestCase):
    """state.py must return defaults, not crash, when state files are corrupted."""

    def _make_store(self, tmp_dir: str):
        """Create a Store backed by a temp directory."""
        from aictl.core.state import StateStore
        return StateStore(Path(tmp_dir))

    def test_load_node_from_clean_state(self):
        """load_node() must return NodeState from a valid state file."""
        from aictl.core.state import NodeState
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            ns = NodeState(profile="nvidia-gpu")
            store.save_node(ns)
            loaded = store.load_node()
            self.assertEqual(loaded.profile, "nvidia-gpu")

    def test_load_node_from_corrupted_json(self):
        """load_node() must return NodeState() if state.json is corrupted JSON."""
        from aictl.core.state import NodeState
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            # Write corrupted JSON to the state file
            (Path(tmp) / "state.json").write_text("{not valid json{{{{")
            result = store.load_node()
            self.assertIsInstance(result, NodeState,
                                  "load_node() must return NodeState() on corrupt JSON")

    def test_load_node_from_truncated_json(self):
        """load_node() must return NodeState() if state.json is truncated."""
        from aictl.core.state import NodeState
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            (Path(tmp) / "state.json").write_text('{"profile": "nvi')  # truncated
            result = store.load_node()
            self.assertIsInstance(result, NodeState)

    def test_load_stacks_from_corrupted_json(self):
        """load_stacks() must return [] if stacks.json is corrupted."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            (Path(tmp) / "stacks.json").write_text("[not json at all]]]")
            result = store.load_stacks()
            self.assertEqual(result, [],
                             "load_stacks() must return [] on corrupt stacks.json")

    def test_load_stacks_from_empty_file(self):
        """load_stacks() must return [] if stacks.json is empty."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            (Path(tmp) / "stacks.json").write_text("")
            result = store.load_stacks()
            self.assertEqual(result, [])

    def test_load_node_missing_file_returns_default(self):
        """load_node() must return NodeState() if state.json doesn't exist."""
        from aictl.core.state import NodeState
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            result = store.load_node()
            self.assertIsInstance(result, NodeState)

    def test_load_stacks_missing_file_returns_empty(self):
        """load_stacks() must return [] if stacks.json doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._make_store(tmp)
            result = store.load_stacks()
            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
