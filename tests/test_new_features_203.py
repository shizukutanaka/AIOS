"""Pass 203: surface FP8 when an FP4 format wins the quantization ranking.

Deployment guidance for Blackwell is consistently "FP8 first, FP4 only if you
need maximum throughput and can validate quality": FP8 is near-lossless (99%
here) with mature framework support, while NVFP4 trades a couple of quality
points for roughly twice the speed.

The scorer legitimately prefers NVFP4 on a B200 — 2.8x versus 1.3x for two
quality points is a defensible trade, and this pass deliberately does **not**
override the ranking. What it fixes is that a user who cares more about
fidelity than tokens/sec had to read the `quant compare` table to discover a
99% option was sitting right there, and got no signal that FP4 quality is
workload-dependent enough to warrant validating.

Mirrors the existing `_q4_k_m_sweet_spot_note` precedent exactly: surface the
runner-up, leave the ranking alone.

Checked while here and deliberately left alone: the underlying table already
matches published comparisons — AWQ (96%) is ranked above GPTQ (91%), which
is the direction 2026 sources report for Llama 3+/Qwen 2+ class models.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout

from aictl.cmd.quant import _fp8_near_lossless_note, run_recommend


def _recommend(gpu, model="llama-3.1-70b", use_json=False, use_case="chat"):
    namespace = argparse.Namespace(model=model, gpu=gpu, use_case=use_case,
                                   json=use_json, vram=0)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = run_recommend(namespace)
    output = buffer.getvalue()
    return code, (json.loads(output) if use_json and code == 0 else output)


class TestNoteFiresOnlyForFP4(unittest.TestCase):
    def test_blackwell_fp4_pick_gets_the_note(self):
        code, payload = _recommend("B200", use_json=True)
        self.assertEqual(code, 0)
        self.assertIn(payload["recommended"]["quant"], ("nvfp4", "mxfp4"))
        self.assertIsNotNone(payload["fp8_note"])

    def test_hopper_awq_pick_does_not(self):
        # AWQ is the right INT4 pick on H100; an FP8 caveat there would be
        # noise, and advice that fires everywhere stops being read.
        code, payload = _recommend("H100", use_json=True)
        self.assertEqual(code, 0)
        self.assertEqual(payload["recommended"]["quant"], "awq")
        self.assertIsNone(payload["fp8_note"])

    def test_note_absent_when_fp8_does_not_fit(self):
        # Pointing at an option that does not fit would be worse than silence.
        scores = [{"quant": "nvfp4", "quality": 0.97, "size_mb": 1000,
                   "speed": 2.8}]
        best = scores[0]
        self.assertIsNone(_fp8_near_lossless_note(scores, best))

    def test_note_absent_when_fp8_itself_wins(self):
        scores = [{"quant": "fp8", "quality": 0.99, "size_mb": 2000, "speed": 1.3}]
        self.assertIsNone(_fp8_near_lossless_note(scores, scores[0]))


class TestNoteContent(unittest.TestCase):
    def setUp(self):
        self.scores = [
            {"quant": "nvfp4", "quality": 0.97, "size_mb": 38297, "speed": 2.8},
            {"quant": "fp8", "quality": 0.99, "size_mb": 70451, "speed": 1.3},
        ]
        self.note = _fp8_near_lossless_note(self.scores, self.scores[0])

    def test_states_fp8_quality(self):
        self.assertIn("99%", self.note)

    def test_names_the_winning_format(self):
        self.assertIn("NVFP4", self.note)

    def test_asks_the_user_to_validate(self):
        # The load-bearing half: FP4 quality is workload-dependent, so
        # "validate" is the actionable instruction, not "FP8 is better".
        self.assertIn("validate", self.note.lower())

    def test_does_not_claim_fp4_is_wrong(self):
        # The ranking stands; this is a trade-off disclosure, not a reversal.
        for word in ("instead of", "should not use", "avoid"):
            self.assertNotIn(word, self.note.lower())


class TestRankingIsUnchanged(unittest.TestCase):
    """The note is advisory — it must not perturb what gets recommended."""

    def test_blackwell_still_recommends_fp4(self):
        _, payload = _recommend("B200", use_json=True)
        self.assertIn(payload["recommended"]["quant"], ("nvfp4", "mxfp4"))

    def test_awq_still_outranks_gptq(self):
        # Matches published comparisons for Llama 3+/Qwen 2+ class models.
        _, payload = _recommend("B200", use_json=True)
        ranked = [payload["recommended"]] + payload["alternatives"]
        order = [entry["quant"] for entry in ranked]
        if "awq" in order and "gptq" in order:
            self.assertLess(order.index("awq"), order.index("gptq"))

    def test_fp8_outranks_fp4_on_quality_in_the_table(self):
        _, payload = _recommend("B200", use_json=True)
        ranked = {e["quant"]: e for e in
                  [payload["recommended"]] + payload["alternatives"]}
        self.assertGreater(ranked["fp8"]["quality"], ranked["nvfp4"]["quality"])


class TestOutputShape(unittest.TestCase):
    def test_json_always_carries_the_key(self):
        # Stable shape: null means "not applicable", never "not evaluated".
        for gpu in ("B200", "H100"):
            _, payload = _recommend(gpu, use_json=True)
            self.assertIn("fp8_note", payload)

    def test_text_output_shows_the_note_on_blackwell(self):
        _, output = _recommend("B200")
        self.assertIn("near-lossless", output)

    def test_text_output_omits_it_on_hopper(self):
        _, output = _recommend("H100")
        self.assertNotIn("near-lossless", output)

    def test_existing_sweet_spot_note_still_present(self):
        # The new note must not have displaced the older one.
        _, payload = _recommend("B200", use_json=True)
        self.assertIsNotNone(payload["sweet_spot_note"])


if __name__ == "__main__":
    unittest.main()
