"""Pass 116 (loop): reject sub-1 --vram budget in `aictl lora auto-tune`.

A VRAM budget is a physical quantity. `lora auto-tune --vram` fed the value
straight into `vram_budget_mb = vram_gb * 1024`, so a negative or zero budget:

  - made the keep/evict test `used + overhead <= budget` ALWAYS false, silently
    evicting every adapter regardless of size, and
  - printed a nonsensical "Used: 0 MB / -5120 MB" line.

The command now validates `--vram >= 1` and exits non-zero with a clear message,
matching the existing convention (`fit` --context/--concurrent, `cost forecast`
--gpus). This is real-CLI reachable: `aictl lora auto-tune m --vram -5`.
"""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout


def _args(vram, base="llama3.1:8b", json=False):
    return argparse.Namespace(base=base, vram=vram, json=json)


class TestLoraAutotuneVramValidation(unittest.TestCase):
    def test_negative_vram_rejected(self):
        from aictl.cmd.lora import run_autotune
        self.assertEqual(run_autotune(_args(-5)), 1)

    def test_zero_vram_rejected(self):
        from aictl.cmd.lora import run_autotune
        self.assertEqual(run_autotune(_args(0)), 1)

    def test_valid_vram_accepted(self):
        from aictl.cmd.lora import run_autotune
        # No adapters registered for this base → returns 0 without touching state.
        self.assertEqual(run_autotune(_args(24, base="no-such-base-xyz")), 0)

    def test_rejected_path_prints_no_negative_budget_line(self):
        from aictl.cmd.lora import run_autotune
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_autotune(_args(-5))
        self.assertEqual(rc, 1)
        # The nonsensical negative-budget summary must never be emitted.
        self.assertNotIn("-5120", buf.getvalue())
        self.assertNotIn("Used:", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
