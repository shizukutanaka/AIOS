"""Pass 180 (IMPROVEMENTS.md item H): Q4_K_M "sweet spot" call-out.

Auditing item H ("Quantization advisor — refresh to the 2026 frontier")
found it was mostly already done and undocumented as such: `QUANT_DATA` in
cmd/quant.py already has an "nvfp4" row (4-bit float, Blackwell,
q_chat=0.97) and the AWQ row already notes "AutoAWQ is deprecated — export
via llm-compressor/GPTQModel" — both proposed additions from the doc. The
one genuinely missing piece was the doc's other ask: "surface the Q4_K_M
'sweet spot' call-out in `quant recommend`" — nothing in the codebase
mentioned "sweet spot" at all.

Fix: `_q4_k_m_sweet_spot_note(scores, best)` in cmd/quant.py returns a
call-out string whenever Q4_K_M fits the GPU/model combination but isn't
the top-scored pick (e.g. a Blackwell GPU where NVFP4 scores higher) --
surfacing the community's most widely-adopted, most portable format even
when the scorer favors something narrower-fit. Returns None (no call-out)
when Q4_K_M itself is the top pick, or when it doesn't fit at all. Wired
into both the human-readable output and the --json body
(`sweet_spot_note` field), per the project's "every command supports
--json" contract.
"""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch


class TestSweetSpotNoteHelper(unittest.TestCase):
    """Pure-function tests: no GPU/model detection involved."""

    def test_none_when_q4_k_m_is_the_top_pick(self):
        from aictl.cmd.quant import _q4_k_m_sweet_spot_note
        scores = [{"quant": "q4_k_m"}, {"quant": "gptq"}]
        note = _q4_k_m_sweet_spot_note(scores, best=scores[0])
        self.assertIsNone(note)

    def test_none_when_q4_k_m_does_not_fit_at_all(self):
        from aictl.cmd.quant import _q4_k_m_sweet_spot_note
        scores = [{"quant": "nvfp4"}, {"quant": "gptq"}]
        note = _q4_k_m_sweet_spot_note(scores, best=scores[0])
        self.assertIsNone(note)

    def test_note_present_when_q4_k_m_fits_but_isnt_top(self):
        from aictl.cmd.quant import _q4_k_m_sweet_spot_note
        scores = [{"quant": "nvfp4"}, {"quant": "q4_k_m"}]
        note = _q4_k_m_sweet_spot_note(scores, best=scores[0])
        self.assertIsNotNone(note)
        self.assertIn("Q4_K_M", note)
        self.assertIn("NVFP4", note)

    def test_note_mentions_portability_and_quality(self):
        from aictl.cmd.quant import _q4_k_m_sweet_spot_note
        scores = [{"quant": "awq"}, {"quant": "q4_k_m"}]
        note = _q4_k_m_sweet_spot_note(scores, best=scores[0])
        self.assertIn("92%", note)
        self.assertIn("CPU", note)


class TestRunRecommendWiring(unittest.TestCase):
    """End-to-end through run_recommend, with GPU detection mocked for
    deterministic scenarios."""

    def _args(self, **overrides):
        base = dict(model="llama3.1:8b", gpu="auto", use_case="chat", json=True)
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_json_includes_sweet_spot_note_field(self):
        from aictl.cmd import quant as quant_mod
        captured = []
        with patch.object(quant_mod, "_detect_gpu", return_value=("RTX 5090", 32768, 100)), \
             patch("aictl.cmd.quant.print_json", side_effect=captured.append):
            quant_mod.run_recommend(self._args())
        self.assertIn("sweet_spot_note", captured[0])

    def test_note_is_none_on_a_gpu_where_q4_k_m_wins(self):
        # cc=0 (older GPU, no NVFP4/AWQ/FP8 eligibility): q4_k_m is the top
        # score, so no call-out is needed. Confirmed deterministic for this
        # exact (model, gpu) fixture -- not a conditional/vacuous check.
        from aictl.cmd import quant as quant_mod
        captured = []
        with patch.object(quant_mod, "_detect_gpu", return_value=("GTX 1080", 8192, 0)), \
             patch("aictl.cmd.quant.print_json", side_effect=captured.append):
            quant_mod.run_recommend(self._args())
        self.assertEqual(captured[0]["recommended"]["quant"], "q4_k_m")
        self.assertIsNone(captured[0]["sweet_spot_note"])

    def test_note_appears_on_a_blackwell_gpu_where_nvfp4_wins(self):
        # cc=100 (Blackwell-class): NVFP4 outscores q4_k_m, so the call-out
        # must appear. Confirmed deterministic for this exact fixture.
        from aictl.cmd import quant as quant_mod
        captured = []
        with patch.object(quant_mod, "_detect_gpu", return_value=("RTX 5090", 32768, 100)), \
             patch("aictl.cmd.quant.print_json", side_effect=captured.append):
            quant_mod.run_recommend(self._args())
        result = captured[0]
        self.assertEqual(result["recommended"]["quant"], "nvfp4")
        self.assertIsNotNone(result["sweet_spot_note"])
        self.assertIn("Q4_K_M", result["sweet_spot_note"])

    def test_human_output_prints_note_when_present(self):
        import io
        from contextlib import redirect_stdout
        from aictl.cmd import quant as quant_mod

        buf = io.StringIO()
        with patch.object(quant_mod, "_detect_gpu", return_value=("RTX 5090", 32768, 100)), \
             redirect_stdout(buf):
            quant_mod.run_recommend(self._args(json=False))
        output = buf.getvalue()
        self.assertIn("NVFP4", output)
        self.assertIn("sweet spot", output.lower())


if __name__ == "__main__":
    unittest.main()
