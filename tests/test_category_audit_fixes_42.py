"""Pass 42 regression tests: snapshot None-stack diff, disagg port constant."""

import pathlib
import unittest


class TestSnapshotDiffNoneNames(unittest.TestCase):
    """_compute_diff must not produce 'Stack removed: None' from nameless entries."""

    def _diff(self, a, b):
        from aictl.cmd.snapshot import _compute_diff
        return _compute_diff(a, b)

    def test_diff_ignores_nameless_stacks_in_a(self):
        """Stack entries without 'name' key must be silently skipped in diff."""
        a = {"stacks": [{"name": "my-stack"}, {"no_name": True}]}
        b = {"stacks": [{"name": "my-stack"}]}
        diffs = self._diff(a, b)
        for d in diffs:
            self.assertNotIn("None", d,
                             "Diff output must not contain the string 'None'")

    def test_diff_ignores_nameless_stacks_in_b(self):
        """Nameless stack in b must not show 'Stack added: None'."""
        a = {"stacks": []}
        b = {"stacks": [{"no_name": True}]}
        diffs = self._diff(a, b)
        for d in diffs:
            self.assertNotIn("Stack added: None", d)

    def test_diff_still_reports_named_stack_changes(self):
        """Named stacks must still appear in diff after the fix."""
        a = {"stacks": [{"name": "old-stack"}]}
        b = {"stacks": [{"name": "new-stack"}]}
        diffs = self._diff(a, b)
        self.assertTrue(any("old-stack" in d for d in diffs),
                        "old-stack removal must appear in diff")
        self.assertTrue(any("new-stack" in d for d in diffs),
                        "new-stack addition must appear in diff")

    def test_diff_empty_stacks_no_crash(self):
        """Empty stack lists must produce no diff entries."""
        diffs = self._diff({"stacks": []}, {"stacks": []})
        self.assertEqual(diffs, [])


class TestDisaggPortUsesConstant(unittest.TestCase):
    """DisaggConfig.port must default to VLLM_DEFAULT_PORT, not a hardcoded 8000."""

    def test_disagg_config_port_matches_constant(self):
        """DisaggConfig().port must equal VLLM_DEFAULT_PORT."""
        from aictl.stack.disagg import DisaggConfig
        from aictl.core.constants import VLLM_DEFAULT_PORT
        cfg = DisaggConfig(model="test-model")
        self.assertEqual(cfg.port, VLLM_DEFAULT_PORT,
                         "DisaggConfig.port must default to VLLM_DEFAULT_PORT")

    def test_disagg_imports_vllm_default_port(self):
        """disagg.py must import VLLM_DEFAULT_PORT from constants (not hardcode 8000)."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "stack" / "disagg.py").read_text()
        self.assertIn("VLLM_DEFAULT_PORT", src,
                      "disagg.py must reference VLLM_DEFAULT_PORT constant")

    def test_disagg_source_not_hardcoded_8000_in_dataclass(self):
        """disagg.py dataclass must not have 'port: int = 8000' hardcoded."""
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "stack" / "disagg.py").read_text()
        self.assertNotIn("port: int = 8000", src,
                         "disagg.py must not hardcode port = 8000 — use VLLM_DEFAULT_PORT")


if __name__ == "__main__":
    unittest.main()
