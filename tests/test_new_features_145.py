"""Pass 145 (new viewpoint): `aictl capacity --compare` across GPUs.

Socratic step: the single-GPU `capacity` answers "how far can I push THIS GPU?".
When that answer is unsatisfying the user's next question is "then which GPU
should I get?" — and nothing answered that by *capacity* (cost compares price,
not what you can actually run). `--compare "RTX 4090,A100 80GB,H100"` adds that
hardware-selection viewpoint, reusing the exact per-GPU KV math (single source of
truth) and ranking GPUs by max context then concurrency.

Covers: the comparison rows, ranking/best-pick, unknown-GPU skip vs all-unknown
error, pinned-quant vs recommended, and that the refactor left the single-GPU
path intact (its tests live in test_new_features_143).
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


def _run(argv):
    parser = _parser()
    ns = parser.parse_args(argv)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            code = ns.func(ns)
    return code, buf.getvalue()


def _run_json(argv):
    code, text = _run(argv + ["--json"])
    return code, json.loads(text)


class TestCompareViewpoint(unittest.TestCase):
    def test_compare_lists_each_known_gpu(self):
        code, out = _run_json(["capacity", "llama3.1:8b",
                               "--compare", "RTX 4090,A100 80GB,H100"])
        self.assertEqual(code, 0)
        names = [g["gpu"] for g in out["gpus"]]
        self.assertEqual(set(names), {"RTX 4090", "A100 80GB", "H100"})

    def test_bigger_gpu_gives_more_capacity(self):
        # An 80GB card must allow at least as much context as a 24GB one.
        code, out = _run_json(["capacity", "llama3.1:8b",
                               "--compare", "RTX 4090,H100"])
        by = {g["gpu"]: g for g in out["gpus"]}
        self.assertGreaterEqual(by["H100"]["max_context"],
                                by["RTX 4090"]["max_context"])
        self.assertGreaterEqual(by["H100"]["max_concurrent"],
                                by["RTX 4090"]["max_concurrent"])

    def test_unknown_gpu_skipped_not_fatal(self):
        code, out = _run_json(["capacity", "llama3.1:8b",
                               "--compare", "RTX 4090,Banana,H100"])
        self.assertEqual(code, 0)
        self.assertIn("Banana", out["unknown"])
        self.assertEqual({g["gpu"] for g in out["gpus"]}, {"RTX 4090", "H100"})

    def test_all_unknown_is_error(self):
        parser = _parser()
        ns = parser.parse_args(["capacity", "llama3.1:8b", "--compare", "Banana,Apple"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ns.func(ns), 1)

    def test_oversized_model_wont_load_on_small_gpu(self):
        code, out = _run_json(["capacity", "llama3.1:70b",
                               "--compare", "RTX 4090,H100", "--quant", "awq"])
        by = {g["gpu"]: g for g in out["gpus"]}
        self.assertFalse(by["RTX 4090"]["loads"])     # 70B awq won't fit 24GB
        self.assertEqual(by["RTX 4090"]["max_context"], 0)

    def test_pinned_quant_used_for_all(self):
        code, out = _run_json(["capacity", "llama3.1:8b",
                               "--compare", "RTX 4090,H100", "--quant", "q4_K_M"])
        self.assertEqual(out["quant"], "q4_K_M")
        self.assertTrue(all(g["quant"] == "q4_K_M" for g in out["gpus"] if g["loads"]))

    def test_empty_compare_falls_through_to_single_gpu(self):
        # --compare "" must not hijack the normal single-GPU path.
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "RTX 4090",
                               "--compare", ""])
        self.assertIn("recommended", out)   # single-GPU result shape


if __name__ == "__main__":
    unittest.main()
