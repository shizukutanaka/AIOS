"""Pass 143 (new feature): `aictl capacity` — the inverse of `aictl fit`.

`fit` answers a binary "will it fit at a context/concurrency I must guess?".
`capacity` answers the question users binary-search `fit` by hand to find:
the MAXIMUM context length and MAXIMUM concurrency a GPU+model can sustain.

It reuses fit's GPU catalog / quant table / weight + overhead math (single
source of truth) but solves for the KV-cache dimension, with two correctness
properties verified here:

  1. Max context is capped at the model's architectural context window — no
     amount of VRAM lets you exceed the trained limit (context_capped flag).
  2. A model whose weights don't fit at any quant reports loads=False, exit 2,
     and a "try a smaller model" note (never a negative/garbage ceiling).

Also covers the realistic (conservative) KV estimate, case-insensitive --quant,
and parse-time rejection of non-positive --context/--concurrent.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest


def _parser():
    from aictl.cmd import capacity
    p = argparse.ArgumentParser(prog="aictl")
    sub = p.add_subparsers()
    capacity.register(sub)
    return p


def _run_json(argv):
    parser = _parser()
    ns = parser.parse_args(argv)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = ns.func(ns)
    return code, json.loads(buf.getvalue())


class TestCapacityMath(unittest.TestCase):
    def test_kv_estimate_is_realistic_not_fit_heuristic(self):
        # ~128 MB/1k for an 8B (calibrated to Llama-class fp16), far above fit's
        # crude ~2 MB/1k. This is what makes the ceilings believable.
        from aictl.cmd.capacity import _kv_per_1k_mb
        self.assertEqual(_kv_per_1k_mb(8), 128)
        self.assertGreaterEqual(_kv_per_1k_mb(1), 8)   # floor

    def test_max_context_solves_budget(self):
        from aictl.cmd.capacity import _max_context_tokens
        # 1280 MB budget, 128 MB/1k, 1 seq -> 10k tokens.
        self.assertEqual(_max_context_tokens(1280, 128, 1), 10000)
        # concurrency halves it.
        self.assertEqual(_max_context_tokens(1280, 128, 2), 5000)

    def test_non_positive_budget_yields_zero(self):
        from aictl.cmd.capacity import _max_context_tokens, _max_concurrent
        self.assertEqual(_max_context_tokens(0, 128, 1), 0)
        self.assertEqual(_max_context_tokens(-500, 128, 1), 0)
        self.assertEqual(_max_concurrent(-1, 128, 8192), 0)

    def test_max_concurrent_solves_budget(self):
        from aictl.cmd.capacity import _max_concurrent
        # 128 MB/1k at 8000-token context -> 1024 MB per sequence.
        self.assertEqual(_max_concurrent(1024, 128, 8000), 1)
        self.assertEqual(_max_concurrent(2048, 128, 8000), 2)
        self.assertEqual(_max_concurrent(2047, 128, 8000), 1)   # floors, never rounds up


class TestCapacityCommand(unittest.TestCase):
    def test_fits_model_reports_positive_ceilings(self):
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "RTX 4090",
                               "--quant", "q4_K_M", "--json"])
        self.assertEqual(code, 0)
        row = out["rows"][0]
        self.assertTrue(row["loads"])
        self.assertGreater(row["max_context"], 0)
        self.assertGreater(row["max_concurrent"], 0)

    def test_context_capped_at_architectural_max(self):
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "RTX 4090",
                               "--quant", "awq", "--json"])
        row = out["rows"][0]
        # awq weights are tiny -> VRAM allows > 128k, so it pins to arch max.
        self.assertLessEqual(row["max_context"], out["arch_max_context"])
        self.assertTrue(row["context_capped"])

    def test_oversized_model_wont_load_exit_2(self):
        code, out = _run_json(["capacity", "llama3.1:70b", "--gpu", "RTX 4090",
                               "--json"])
        self.assertEqual(code, 2)
        self.assertTrue(all(not r["loads"] for r in out["rows"]))
        self.assertTrue(all(r["max_context"] == 0 for r in out["rows"]))
        self.assertTrue(out["notes"])   # "try a smaller model"

    def test_quant_is_case_insensitive(self):
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "RTX 4090",
                               "--quant", "q4_k_m", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(out["rows"][0]["quant"], "q4_K_M")

    def test_all_quants_listed_by_default(self):
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "RTX 4090",
                               "--json"])
        from aictl.cmd.fit import QUANT_CONFIGS
        self.assertEqual(len(out["rows"]), len(QUANT_CONFIGS))


class TestCapacityValidation(unittest.TestCase):
    def _expect_exit2(self, argv):
        parser = _parser()
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(argv)
        self.assertEqual(cm.exception.code, 2)

    def test_context_zero_rejected(self):
        self._expect_exit2(["capacity", "llama3.1:8b", "--context", "0"])

    def test_concurrent_negative_rejected(self):
        self._expect_exit2(["capacity", "llama3.1:8b", "--concurrent", "-2"])

    def test_unknown_model_returns_1(self):
        parser = _parser()
        ns = parser.parse_args(["capacity", "nope-not-a-model", "--gpu", "RTX 4090"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ns.func(ns), 1)

    def test_unknown_quant_returns_1(self):
        parser = _parser()
        ns = parser.parse_args(["capacity", "llama3.1:8b", "--gpu", "RTX 4090",
                                "--quant", "zzz"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ns.func(ns), 1)


if __name__ == "__main__":
    unittest.main()
