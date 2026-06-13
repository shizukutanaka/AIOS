"""Pass 85 (loop): mig plan --json emits valid JSON on the no-MIG-GPU path.

Found by a real-CLI --json contract sweep: `aictl --json mig plan` on a box
without MIG-capable GPUs printed human text ("✗ No MIG-capable GPUs detected")
instead of JSON, breaking machine consumers. The error path now emits a
parseable object while preserving rc=1 and the human-readable message.
"""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch, MagicMock


class TestMigPlanJsonNoGpu(unittest.TestCase):

    def test_no_mig_gpu_json_is_parseable(self):
        from aictl.cmd.mig import run_plan
        fake_hw = MagicMock()
        fake_hw.gpus = []  # no GPUs at all → no MIG-capable
        captured = []
        with patch("aictl.cmd.mig.full_detect", return_value=fake_hw), \
             patch("aictl.cmd.mig.print_json", side_effect=captured.append):
            args = argparse.Namespace(models=None, json=True)
            rc = run_plan(args)
        self.assertEqual(rc, 1)  # still signals "no plan produced"
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0]["mig_capable"])
        self.assertEqual(captured[0]["plans"], [])
        self.assertIn("MIG-capable", captured[0]["error"])

    def test_no_mig_gpu_human_uses_err(self):
        from aictl.cmd.mig import run_plan
        fake_hw = MagicMock()
        fake_hw.gpus = []
        with patch("aictl.cmd.mig.full_detect", return_value=fake_hw), \
             patch("aictl.cmd.mig.err") as mock_err, \
             patch("aictl.cmd.mig.print_json") as mock_json:
            args = argparse.Namespace(models=None, json=False)
            rc = run_plan(args)
        self.assertEqual(rc, 1)
        mock_err.assert_called_once()
        mock_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
