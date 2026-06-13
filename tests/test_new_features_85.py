"""Pass 85 (loop): mig plan --json emits valid JSON on the no-MIG-GPU path.

Found by a real-CLI --json contract sweep: `aictl --json mig plan` on a box
without MIG-capable GPUs printed human text ("✗ No MIG-capable GPUs detected")
instead of JSON, breaking machine consumers. The error path now emits a
parseable object while preserving rc=1 and the human-readable message.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock


class TestJsonFlagPlacement(unittest.TestCase):
    """`--json` must work in ANY position, including trailing on subcommands that
    do not declare their own --json (e.g. `aictl cost forecast --json`).

    Before: argparse rejected the trailing form with "unrecognized arguments:
    --json" for ~45 subcommands, while `aictl --json cost forecast` and
    `aictl events list --json` worked — an inconsistent surface. main() now
    strips standalone --json before parsing and re-derives it from argv.
    """

    def _run(self, argv: list[str]) -> tuple[int, str]:
        from aictl.__main__ import main
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as sd, \
             patch.dict(os.environ, {"AIOS_STATE_DIR": sd}), \
             patch.object(sys, "argv", argv), redirect_stdout(buf):
            rc = main()
        return rc, buf.getvalue()

    def test_trailing_json_on_command_without_own_flag(self):
        # snapshot list does not declare its own --json; trailing must still work.
        rc, out = self._run(["aictl", "snapshot", "list", "--json"])
        self.assertEqual(rc, 0)
        self.assertIsInstance(json.loads(out), list)

    def test_global_json_form(self):
        rc, out = self._run(["aictl", "--json", "snapshot", "list"])
        self.assertEqual(rc, 0)
        json.loads(out)

    def test_middle_json_form(self):
        rc, out = self._run(["aictl", "cost", "--json", "forecast", "--gpu", "RTX_4090"])
        self.assertEqual(rc, 0)
        json.loads(out)

    def test_no_json_stays_human(self):
        rc, out = self._run(["aictl", "snapshot", "list"])
        self.assertEqual(rc, 0)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out)


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
