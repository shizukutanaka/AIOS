"""Pass 87 (loop): fit's fp16 base must derive from the parameter count.

Functional correctness bug found by inspecting real output: `aictl fit
llama3.1:8b` reported fp16 ≈ 6 GB. The DB's vram_required_mb is the model's
*recommended-quantization* footprint (q4_K_M for the 8B), but fit treated it as
the fp16 base and multiplied down — under-reporting every quant by ~2.5x and,
worse, telling users an oversized model "fits" at fp16. fp16 now = params × 2
bytes, so the "will it fit?" answer is correct.
"""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch


def _fit(model: str, gpu: str, context: int = 8192):
    from aictl.cmd.fit import run
    captured = []
    with patch("aictl.cmd.fit.print_json", side_effect=captured.append):
        rc = run(argparse.Namespace(model=model, gpu=gpu, context=context,
                                    concurrent=1, use_case="", json=True))
    return rc, captured[0]


class TestFitFp16Base(unittest.TestCase):

    def test_8b_fp16_weights_match_param_count(self):
        # 8B params × 2 bytes/param × 1024 MB/GB = 16384 MB of fp16 weights.
        _, d = _fit("llama3.1:8b", "H100")
        self.assertEqual(d["quants"]["fp16"]["weights_mb"], 8 * 2 * 1024)

    def test_fp16_is_double_fp8_weights(self):
        # The quant multipliers are fractions of fp16: fp8 (0.50) must be half.
        _, d = _fit("llama3.1:8b", "H100")
        self.assertEqual(d["quants"]["fp16"]["weights_mb"],
                         2 * d["quants"]["fp8"]["weights_mb"])

    def test_70b_fp16_does_not_fit_single_h100(self):
        # 70B fp16 ≈ 140 GB — must NOT fit an 80 GB H100 (the whole point).
        rc, d = _fit("llama3.1:70b", "H100")
        self.assertFalse(d["quants"]["fp16"]["fits"])
        self.assertGreater(d["quants"]["fp16"]["weights_mb"], 80 * 1024)
        # A quantized form should still fit, so the command reports success.
        self.assertTrue(any(q["fits"] for q in d["quants"].values()))

    def test_70b_fp16_weights_match_param_count(self):
        _, d = _fit("llama3.1:70b", "H100")
        self.assertEqual(d["quants"]["fp16"]["weights_mb"], 70 * 2 * 1024)


if __name__ == "__main__":
    unittest.main()
