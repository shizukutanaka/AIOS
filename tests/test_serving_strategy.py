"""Tests for the serving-strategy advisor (P/D-disagg vs aggregated vs AFD)."""

from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.serving_strategy import (
    detect_model_type, recommend_strategy, strategy_matrix,
    VALID_OBJECTIVES, StrategyRecommendation,
)
from aictl.cmd import deploy


class TestModelTypeDetection(unittest.TestCase):
    def test_moe_families_detected(self):
        for m in ("mistralai/Mixtral-8x7B", "deepseek-ai/DeepSeek-V3",
                  "Qwen/Qwen3-MoE-A3B", "databricks/dbrx-instruct",
                  "llama4-scout", "some-model-moe"):
            self.assertEqual(detect_model_type(m), "moe", m)

    def test_dense_models_detected(self):
        for m in ("meta-llama/Llama-3.1-8B", "Qwen/Qwen2.5-7B",
                  "google/gemma-2-9b", "microsoft/phi-4"):
            self.assertEqual(detect_model_type(m), "dense", m)


class TestStrategyDecision(unittest.TestCase):
    def test_small_dense_is_aggregated(self):
        rec = recommend_strategy("llama-3.1-8b", gpu_count=1, objective="balanced")
        self.assertEqual(rec.strategy, "aggregated")
        self.assertIn("--enable-chunked-prefill", rec.vllm_flags)

    def test_moe_latency_multigpu_is_afd(self):
        rec = recommend_strategy("mixtral-8x22b", gpu_count=8, objective="latency")
        self.assertEqual(rec.strategy, "afd")
        self.assertIn("--enable-expert-parallel", rec.vllm_flags)
        self.assertEqual(rec.model_type, "moe")

    def test_large_dense_throughput_is_pd_disagg(self):
        rec = recommend_strategy("llama-3.1-70b", gpu_count=8, objective="throughput")
        self.assertEqual(rec.strategy, "pd-disagg")
        self.assertTrue(any("kv-transfer-config" in f for f in rec.vllm_flags))

    def test_throughput_two_gpu_is_pd_disagg(self):
        rec = recommend_strategy("llama-3.1-8b", gpu_count=2, objective="throughput")
        self.assertEqual(rec.strategy, "pd-disagg")

    def test_moe_single_gpu_falls_back_to_aggregated(self):
        # MoE but only 1 GPU → can't AFD → aggregated with EP flag
        rec = recommend_strategy("mixtral-8x7b", gpu_count=1, objective="latency")
        self.assertEqual(rec.strategy, "aggregated")
        self.assertIn("--enable-expert-parallel", rec.vllm_flags)

    def test_model_type_override(self):
        # Force MoE on a name that wouldn't be detected
        rec = recommend_strategy("custom-model", gpu_count=8,
                                 objective="latency", model_type="moe")
        self.assertEqual(rec.strategy, "afd")

    def test_pd_disagg_replica_split_in_command(self):
        rec = recommend_strategy("llama-3.1-70b", gpu_count=6, objective="throughput")
        self.assertIn("--prefill-replicas", rec.next_command)
        self.assertIn("--decode-replicas", rec.next_command)

    def test_invalid_objective_defaults_balanced(self):
        rec = recommend_strategy("llama-3.1-8b", gpu_count=1, objective="bogus")
        self.assertEqual(rec.objective, "balanced")

    def test_zero_gpu_clamped_to_one(self):
        rec = recommend_strategy("llama-3.1-8b", gpu_count=0)
        self.assertEqual(rec.gpu_count, 1)

    def test_every_recommendation_has_references_and_command(self):
        for model, n, obj in [("llama-8b", 1, "balanced"),
                              ("mixtral-8x7b", 8, "latency"),
                              ("llama-70b", 8, "throughput")]:
            rec = recommend_strategy(model, gpu_count=n, objective=obj)
            self.assertTrue(rec.references)
            self.assertTrue(rec.next_command.startswith("aictl deploy"))
            self.assertIn(rec.strategy, ("aggregated", "pd-disagg", "afd"))

    def test_matrix_lists_all_three(self):
        strategies = {row["strategy"] for row in strategy_matrix()}
        self.assertEqual(strategies, {"aggregated", "pd-disagg", "afd"})


class TestStrategyCLI(unittest.TestCase):
    def _run(self, **ns):
        ns.setdefault("model_type", "auto")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = deploy.run_strategy(argparse.Namespace(**ns))
        return rc, buf.getvalue()

    def test_json_output_shape(self):
        rc, out = self._run(model="mixtral-8x22b", gpu_count=8,
                            objective="latency", json=True)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["strategy"], "afd")
        self.assertIn("vllm_flags", data)
        self.assertIn("next_command", data)

    def test_text_output_runs(self):
        rc, out = self._run(model="llama-3.1-8b", gpu_count=1,
                            objective="balanced", json=False)
        self.assertEqual(rc, 0)
        self.assertIn("AGGREGATED", out)

    def test_registered_in_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["deploy", "strategy", "llama-8b",
                             "--gpu-count", "4", "--objective", "throughput"])
        self.assertEqual(args.deploy_cmd, "strategy")
        self.assertEqual(args.gpu_count, 4)


if __name__ == "__main__":
    unittest.main()
