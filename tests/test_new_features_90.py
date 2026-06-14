"""Pass 90 (loop): spec profile and spec recommend agree on speedup.

Functional inconsistency: for the identical (70b, 1b, acc=0.82, gamma=5) pair,
`spec recommend` reported 3.0x (via _Pair.speedup: accepted tokens / draft
overhead + the guaranteed bonus token, capped at 3.0) while `spec profile`
reported 2.73x (a separate formula that dropped the bonus token and the cap and
used a fixed overhead). Two numbers for one physical quantity. run_profile now
reuses the canonical _Pair.speedup() for known pairs, and a consistent
bonus-token + cap shape for unknown ones.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock


def _profile(target: str, draft: str):
    from aictl.cmd import spec
    fake = MagicMock(ttft_ms_p95=0.0, tokens_per_sec=0.0)
    buf = io.StringIO()
    with patch("aictl.cmd.spec.run_benchmark", return_value=fake), redirect_stdout(buf):
        spec.run_profile(argparse.Namespace(target=target, draft=draft,
                                            endpoint="http://x", requests=1, json=True))
    return json.loads(buf.getvalue())


def _recommend_speedup(target: str, draft: str):
    from aictl.cmd.spec import PAIRS
    p = next(p for p in PAIRS if p.target == target and p.draft == draft)
    return round(p.speedup(), 2)


class TestSpecProfileRecommendAgree(unittest.TestCase):

    def test_known_pair_profile_matches_recommend(self):
        prof = _profile("llama3.1:70b", "llama3.2:1b")["estimated_speedup"]
        rec = _recommend_speedup("llama3.1:70b", "llama3.2:1b")
        self.assertEqual(prof, rec)

    def test_known_pair_uses_pair_gamma_and_acceptance(self):
        from aictl.cmd.spec import PAIRS
        pair = next(p for p in PAIRS
                    if p.target == "llama3.1:70b" and p.draft == "llama3.2:1b")
        d = _profile("llama3.1:70b", "llama3.2:1b")
        self.assertEqual(d["estimated_acceptance_rate"], round(pair.acceptance_rate, 3))
        self.assertEqual(d["estimated_gamma"], pair.gamma)

    def test_unknown_pair_speedup_capped_and_above_one(self):
        d = _profile("vendor/Unknown-7B", "vendor/draft-1b")
        self.assertGreater(d["estimated_speedup"], 1.0)
        self.assertLessEqual(d["estimated_speedup"], 3.0)


if __name__ == "__main__":
    unittest.main()
