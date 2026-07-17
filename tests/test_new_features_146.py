"""Pass 146: `mig plan --models` must reject non-positive VRAM specs.

`run_plan` parsed `model:vram_gb` pairs with `int(parts[1])`, which silently
accepts a NEGATIVE or ZERO value ("llama3:-16", "llama3:0"). A non-positive VRAM
requirement is meaningless and "fits" every MIG partition
(`p.memory_gb >= model.vram_gb` is always true), so the planner assigns the model
the smallest slice and mis-reports utilization/waste. Non-numeric specs already
fell back to a default; a negative number slipped through because `int("-16")`
succeeds.

The parse was also inline in run_plan, behind a MIG-capability early-return, so it
was unreachable on non-MIG hosts and untestable. Extracted to `_parse_model_specs`
which now floors any non-positive (or unparseable) VRAM to the default.
"""

from __future__ import annotations

import unittest


def _specs(specs):
    from aictl.cmd.mig import _parse_model_specs
    return [(m.name, m.vram_gb) for m in _parse_model_specs(specs)]


class TestMigModelSpecParse(unittest.TestCase):
    def test_normal_pairs(self):
        self.assertEqual(_specs(["llama3:16", "qwen:8"]),
                         [("llama3", 16), ("qwen", 8)])

    def test_negative_vram_defaults(self):
        self.assertEqual(_specs(["bad:-16", "ok:8"]), [("bad", 16), ("ok", 8)])

    def test_zero_vram_defaults(self):
        self.assertEqual(_specs(["z:0"]), [("z", 16)])

    def test_non_numeric_vram_defaults(self):
        self.assertEqual(_specs(["x:abc"]), [("x", 16)])

    def test_missing_vram_defaults(self):
        self.assertEqual(_specs(["justname"]), [("justname", 16)])

    def test_none_uses_builtin_default(self):
        self.assertEqual(_specs(None), [("llama3", 16), ("embedding", 2)])

    def test_all_requirements_positive(self):
        # The whole point: no requirement is ever <= 0 after parsing.
        for _name, vram in _specs(["a:-1", "b:0", "c:abc", "d:32", "e"]):
            self.assertGreater(vram, 0)


if __name__ == "__main__":
    unittest.main()
