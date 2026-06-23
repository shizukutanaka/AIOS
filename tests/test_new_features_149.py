"""Pass 149 (new viewpoint): `aictl capacity --pack` — multi-model co-location.

Socratic step: `capacity` (and `fit`) reason about ONE model; `mig` partitions
hardware. But the project ships a `multi-model` recipe, and those operators ask a
question nothing answered: "do model A AND model B fit on this GPU together, and
what's the VRAM breakdown?". `--pack` adds that co-location viewpoint, summing
each model's weights (at its recommended or pinned quant) + KV at --context/
--concurrent against the GPU's usable VRAM, and exits 2 when they don't co-fit.

Covers: fitting vs non-fitting sets, exit codes, pinned-quant, unknown-model
skip, the all-fp16 over-budget case, and that adding a model never shrinks the
total footprint.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest


def _run_json(argv):
    from aictl.cmd import capacity
    p = argparse.ArgumentParser(prog="aictl")
    sub = p.add_subparsers()
    capacity.register(sub)
    ns = p.parse_args(argv + ["--json"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(io.StringIO()):
            code = ns.func(ns)
    return code, json.loads(buf.getvalue())


class TestPackViewpoint(unittest.TestCase):
    def test_two_small_quant_models_fit(self):
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "RTX 4090",
                               "--pack", "qwen2.5:7b", "--quant", "q4_K_M"])
        self.assertEqual(code, 0)
        self.assertTrue(out["fits"])
        self.assertEqual(len(out["models"]), 2)
        self.assertGreater(out["headroom_mb"], 0)

    def test_two_fp16_models_overflow_exit_2(self):
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "RTX 4090",
                               "--pack", "qwen2.5:7b", "--quant", "fp16"])
        self.assertEqual(code, 2)
        self.assertFalse(out["fits"])
        self.assertLess(out["headroom_mb"], 0)

    def test_total_is_sum_of_footprints(self):
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "H100",
                               "--pack", "qwen2.5:7b", "--quant", "q4_K_M"])
        self.assertEqual(out["total_mb"],
                         sum(m["footprint_mb"] for m in out["models"]))

    def test_each_footprint_includes_weights_and_kv(self):
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "H100",
                               "--pack", "qwen2.5:7b", "--quant", "q4_K_M"])
        for m in out["models"]:
            self.assertGreater(m["weights_mb"], 0)
            self.assertGreaterEqual(m["kv_mb"], 0)
            self.assertGreater(m["footprint_mb"], m["weights_mb"])

    def test_unknown_model_skipped(self):
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "RTX 4090",
                               "--pack", "nonexistent-xyz", "--quant", "q4_K_M"])
        self.assertIn("nonexistent-xyz", out["unknown"])
        self.assertEqual([m["model"] for m in out["models"]], ["llama3.1:8b"])

    def test_adding_a_model_grows_total(self):
        _, one = _run_json(["capacity", "llama3.1:8b", "--gpu", "H100",
                            "--quant", "q4_K_M", "--pack", "qwen2.5:7b"])
        _, two = _run_json(["capacity", "llama3.1:8b", "--gpu", "H100",
                            "--quant", "q4_K_M", "--pack", "qwen2.5:7b,llama3.2:1b"])
        self.assertGreater(two["total_mb"], one["total_mb"])
        self.assertEqual(len(two["models"]), 3)

    def test_pinned_quant_applied_to_all(self):
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "H100",
                               "--pack", "qwen2.5:7b", "--quant", "awq"])
        self.assertTrue(all(m["quant"] == "awq" for m in out["models"]))

    def test_pack_does_not_affect_single_gpu_shape(self):
        # Without --pack the result keeps the single-GPU shape (has 'rows').
        code, out = _run_json(["capacity", "llama3.1:8b", "--gpu", "RTX 4090"])
        self.assertIn("rows", out)
        self.assertNotIn("models", out)


if __name__ == "__main__":
    unittest.main()
