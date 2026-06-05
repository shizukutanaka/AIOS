"""Tests for OS-level LLM features: token metering, isolation, LoRA."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.core.metering import TokenMeter, TokenBucket
from aictl.runtime.isolation import (
    IsolationConfig, generate_systemd_slice,
    generate_isolation_for_model, detect_cpu_isolation_support,
)
from aictl.runtime.lora import LoRAManager, BaseModel, LoRAAdapter


class TestTokenMeter(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.meter = TokenMeter(self.tmp)

    def test_record_and_retrieve(self):
        ok = self.meter.record("key1", "llama3", 100, 50)
        self.assertTrue(ok)
        usage = self.meter.get_usage("key1")
        self.assertEqual(usage.prompt_tokens, 100)
        self.assertEqual(usage.completion_tokens, 50)
        self.assertEqual(usage.total_tokens, 150)
        self.assertEqual(usage.request_count, 1)

    def test_accumulation(self):
        self.meter.record("key2", "llama3", 100, 50)
        self.meter.record("key2", "llama3", 200, 100)
        usage = self.meter.get_usage("key2")
        self.assertEqual(usage.total_tokens, 450)
        self.assertEqual(usage.request_count, 2)

    def test_daily_quota_enforcement(self):
        self.meter.set_quota("limited", per_day=100)
        ok1 = self.meter.record("limited", "llama3", 50, 30)
        self.assertTrue(ok1)  # 80 tokens, under 100
        ok2 = self.meter.record("limited", "llama3", 50, 30)
        self.assertFalse(ok2)  # Would be 160, over 100

    def test_cost_estimation(self):
        self.meter.record("cost-test", "llama3", 1_000_000, 500_000)
        cost = self.meter.estimate_cost("cost-test")
        self.assertGreater(cost, 0)

    def test_list_usage(self):
        self.meter.record("a", "m", 10, 5)
        self.meter.record("b", "m", 20, 10)
        buckets = self.meter.list_usage()
        self.assertEqual(len(buckets), 2)

    def test_empty_usage(self):
        self.assertIsNone(self.meter.get_usage("nonexistent"))


class TestIsolation(unittest.TestCase):
    def test_generate_slice(self):
        config = IsolationConfig(
            name="llama3-8b", memory_min_gb=8.0, memory_max_gb=16.0,
            oom_score_adj=-900, nice=-10,
        )
        slice_content = generate_systemd_slice(config)
        self.assertIn("[Slice]", slice_content)
        self.assertIn("MemoryMin=", slice_content)
        self.assertIn("OOMScoreAdjust=-900", slice_content)

    def test_generate_for_model_gpu(self):
        config = generate_isolation_for_model("llama3:8b", 8.0, vram_gb=24)
        self.assertGreater(config.memory_min_gb, 0)
        self.assertEqual(config.oom_score_adj, -900)

    def test_generate_for_model_cpu(self):
        config = generate_isolation_for_model("llama3:8b", 8.0, vram_gb=0)
        # CPU mode needs more RAM reserved
        self.assertGreater(config.memory_min_gb, 0)

    def test_detect_support(self):
        support = detect_cpu_isolation_support()
        self.assertIn("cgroup_v2", support)
        self.assertIn("numa", support)


class TestLoRA(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mgr = LoRAManager(self.tmp)

    def test_register_and_list(self):
        self.mgr.register_base(BaseModel(name="llama3-8b", vram_mb=16384))
        self.mgr.register_adapter(LoRAAdapter(
            name="finance", base_model="llama3-8b",
            path="/models/finance-lora", rank=16,
        ))
        adapters = self.mgr.list_adapters("llama3-8b")
        self.assertEqual(len(adapters), 1)
        self.assertEqual(adapters[0].name, "finance")

    def test_vram_budget(self):
        self.mgr.register_base(BaseModel(name="llama3-8b", vram_mb=16384))
        self.mgr.register_adapter(LoRAAdapter(
            name="a1", base_model="llama3-8b", vram_overhead_mb=100))
        self.mgr.register_adapter(LoRAAdapter(
            name="a2", base_model="llama3-8b", vram_overhead_mb=150))
        budget = self.mgr.vram_budget("llama3-8b")
        self.assertEqual(budget["base_vram_mb"], 16384)
        self.assertEqual(budget["adapter_vram_mb"], 250)
        self.assertEqual(budget["active_adapters"], 2)

    def test_vllm_args(self):
        self.mgr.register_adapter(LoRAAdapter(
            name="fin", base_model="llama3", path="/lora/fin", rank=16))
        args = self.mgr.generate_vllm_args("llama3")
        self.assertIn("--enable-lora", args)
        self.assertTrue(any("fin=/lora/fin" in a for a in args))

    def test_sglang_args(self):
        self.mgr.register_adapter(LoRAAdapter(
            name="code", base_model="llama3", path="/lora/code"))
        args = self.mgr.generate_sglang_args("llama3")
        self.assertTrue(any("/lora/code" in a for a in args))

    def test_empty(self):
        self.assertEqual(len(self.mgr.list_adapters()), 0)
        self.assertEqual(self.mgr.vram_budget("nonexistent")["total_vram_mb"], 0)


class TestOSFeaturesCLI(unittest.TestCase):
    def test_meter_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["meter", "usage"])
        self.assertEqual(args.meter_cmd, "usage")

    def test_lora_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["lora", "add", "my-lora", "--base", "llama3", "--rank", "32"])
        self.assertEqual(args.name, "my-lora")
        self.assertEqual(args.base, "llama3")
        self.assertEqual(args.rank, 32)

    def test_report_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["report"])
        self.assertEqual(args.command, "report")


if __name__ == "__main__":
    unittest.main()
