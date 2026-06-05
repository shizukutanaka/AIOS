"""Tests for Phase 6: model recommendation, benchmark, setup wizard."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.recommend import recommend, recommend_for_profile, MODELS, ModelRec
from aictl.runtime.benchmark import BenchResult, BENCH_PROMPTS


class TestModelRecommend(unittest.TestCase):
    def test_cpu_only_returns_ollama(self):
        recs = recommend(vram_mb=0, ram_mb=16384)
        for r in recs:
            self.assertEqual(r.runtime, "ollama")

    def test_small_gpu_filters_large_models(self):
        recs = recommend(vram_mb=4096, ram_mb=16384)
        for r in recs:
            self.assertLessEqual(r.vram_required_mb, 4096)

    def test_large_gpu_includes_vllm(self):
        recs = recommend(vram_mb=24576, ram_mb=32768)
        runtimes = {r.runtime for r in recs}
        # Should include both ollama and vllm options
        self.assertGreater(len(recs), 0)

    def test_use_case_filter(self):
        recs = recommend(vram_mb=24576, ram_mb=32768, use_case="code")
        for r in recs:
            self.assertEqual(r.use_case, "code")

    def test_embedding_filter(self):
        recs = recommend(vram_mb=8192, ram_mb=16384, use_case="embedding")
        for r in recs:
            self.assertEqual(r.use_case, "embedding")

    def test_no_results_for_impossible_constraints(self):
        recs = recommend(vram_mb=0, ram_mb=512)
        # Very low RAM — might return nothing
        for r in recs:
            self.assertLessEqual(r.ram_required_mb, 512)

    def test_max_results(self):
        recs = recommend(vram_mb=80000, ram_mb=128000, max_results=3)
        self.assertLessEqual(len(recs), 3)

    def test_profile_parsing(self):
        recs = recommend_for_profile("nvidia-ada-24gb", ram_mb=32768)
        for r in recs:
            self.assertLessEqual(r.vram_required_mb, 24 * 1024)

    def test_profile_cpu_only(self):
        recs = recommend_for_profile("cpu-only", ram_mb=16384)
        for r in recs:
            self.assertEqual(r.runtime, "ollama")

    def test_models_database_valid(self):
        for m in MODELS:
            self.assertIsInstance(m, ModelRec)
            self.assertGreater(len(m.name), 0)
            self.assertIn(m.runtime, ("ollama", "vllm", "sglang"))
            self.assertIn(m.use_case, ("chat", "code", "embedding", "vision", "stt", "reasoning"))
            self.assertGreater(m.vram_required_mb, 0)


class TestBenchmark(unittest.TestCase):
    def test_bench_result_defaults(self):
        r = BenchResult()
        self.assertEqual(r.requests, 0)
        self.assertEqual(r.errors, 0)
        self.assertEqual(r.tokens_per_sec, 0.0)

    def test_bench_prompts_exist(self):
        self.assertGreater(len(BENCH_PROMPTS), 3)

    def test_bench_unreachable(self):
        from aictl.runtime.benchmark import run_benchmark
        result = run_benchmark(
            endpoint="http://localhost:99999",
            num_requests=1,
            timeout=2,
        )
        self.assertEqual(result.errors, 1)


class TestNewCLICommands(unittest.TestCase):
    def test_recommend_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["recommend", "--use-case", "code", "--top", "3"])
        self.assertEqual(args.command, "recommend")
        self.assertEqual(args.use_case, "code")
        self.assertEqual(args.top, 3)

    def test_bench_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["bench", "--endpoint", "http://localhost:8000",
                             "--model", "llama3", "-n", "10"])
        self.assertEqual(args.command, "bench")
        self.assertEqual(args.endpoint, "http://localhost:8000")
        self.assertEqual(args.requests, 10)

    def test_setup_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["setup"])
        self.assertEqual(args.command, "setup")

    def test_all_19_commands(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        simple = ["init", "doctor", "ps", "serve", "status", "setup", "recommend"]
        for cmd in simple:
            args = p.parse_args([cmd])
            self.assertEqual(args.command, cmd, f"Command '{cmd}' failed")


if __name__ == "__main__":
    unittest.main()
