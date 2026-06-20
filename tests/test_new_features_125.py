"""Pass 125 (loop): close the negative-slice trap class — remaining count flags.

A sweep for `[:n]`/`[-n:]` with user-controlled counts found three more sites
beyond Pass 112/121/124, all reachable via the real CLI with a negative flag:

  - `recommend --top -3` → `candidates[:-3]` returned catalog_size - 3 models
    (13 of 16), the inverse of "top N";
  - `optimize --top -3`  → `all_recs[:-3]` returned all-but-last-3 tuning recs;
  - `route test --n -3`  → `_TEST_CASES[:-3]` ran 9 of 12 cases, MORE than asked.

(`diff --n` and `snapshot --keep` were already guarded with `if n > 0` /
`max(0, ...)`, so they were not affected.)

Fixes: the shared `recommend()` library returns [] for max_results <= 0
(defense in depth — `fit` also calls it), and the three CLI handlers reject a
count < 1 with a clear message and non-zero exit.
"""

from __future__ import annotations

import argparse
import unittest


class TestRecommendLibGuard(unittest.TestCase):
    def test_negative_max_results_returns_empty(self):
        from aictl.runtime.recommend import recommend
        self.assertEqual(recommend(vram_mb=80000, max_results=-3), [])

    def test_zero_max_results_returns_empty(self):
        from aictl.runtime.recommend import recommend
        self.assertEqual(recommend(vram_mb=80000, max_results=0), [])

    def test_positive_max_results_capped(self):
        from aictl.runtime.recommend import recommend
        recs = recommend(vram_mb=80000, max_results=5)
        self.assertLessEqual(len(recs), 5)
        self.assertGreater(len(recs), 0)


class TestCountFlagValidation(unittest.TestCase):
    def test_recommend_negative_top_rejected(self):
        from aictl.cmd.recommend import run
        self.assertEqual(run(argparse.Namespace(top=-3, use_case="",
                                                json=False)), 1)

    def test_recommend_zero_top_rejected(self):
        from aictl.cmd.recommend import run
        self.assertEqual(run(argparse.Namespace(top=0, use_case="",
                                                json=False)), 1)

    def test_optimize_negative_top_rejected(self):
        from aictl.cmd.optimize import run
        self.assertEqual(run(argparse.Namespace(top=-3, engine="",
                                                state_dir=None, json=False)), 1)

    def test_route_test_negative_n_rejected(self):
        from aictl.cmd.route import run_test
        self.assertEqual(run_test(argparse.Namespace(n=-3, json=False)), 1)


if __name__ == "__main__":
    unittest.main()
