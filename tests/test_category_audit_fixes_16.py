"""Pass 16 regression tests for correctness bugs identified by deep audit."""

import unittest


class TestWarmupStatusConsistency(unittest.TestCase):
    """warmup.py: vLLM/SGLang skip status must use 'skipped' not 'skip'."""

    def test_vllm_engine_status_is_skipped(self):
        from aictl.runtime.warmup import WarmupManager, UsageRecord
        import tempfile
        from pathlib import Path
        from aictl.core.state import StateStore

        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            mgr = WarmupManager(store)
            candidates = [UsageRecord(model="llama3:8b", engine="vllm", count=5)]
            results = mgr.warmup(candidates)

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0]["status"], "skipped",
            "vLLM warmup must emit status='skipped', not 'skip', "
            "to match the default status for unknown engines",
        )

    def test_sglang_engine_status_is_skipped(self):
        from aictl.runtime.warmup import WarmupManager, UsageRecord
        import tempfile
        from pathlib import Path
        from aictl.core.state import StateStore

        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            mgr = WarmupManager(store)
            candidates = [UsageRecord(model="llama3:8b", engine="sglang", count=3)]
            results = mgr.warmup(candidates)

        self.assertEqual(results[0]["status"], "skipped")

    def test_all_skipped_statuses_are_consistent(self):
        from aictl.runtime.warmup import WarmupManager, UsageRecord
        import tempfile
        from pathlib import Path
        from aictl.core.state import StateStore

        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            mgr = WarmupManager(store)
            candidates = [
                UsageRecord(model="m1", engine="vllm", count=5),
                UsageRecord(model="m2", engine="sglang", count=2),
                UsageRecord(model="m3", engine="trt-llm", count=1),
            ]
            results = mgr.warmup(candidates)

        statuses = {r["status"] for r in results}
        self.assertNotIn("skip", statuses, "Status 'skip' must not appear — use 'skipped'")


class TestMeteringSetQuotaZeroResetsToUnlimited(unittest.TestCase):
    """metering.py: set_quota(per_day=0) must reset the quota to unlimited (0)."""

    def test_quota_can_be_reset_to_zero(self):
        import tempfile
        from pathlib import Path
        from aictl.core.metering import TokenMeter

        with tempfile.TemporaryDirectory() as d:
            meter = TokenMeter(Path(d))
            # Set a non-zero quota
            meter.set_quota("user1", per_day=5000)
            bucket = meter.get_usage("user1")
            self.assertEqual(bucket.quota_tokens_per_day, 5000)

            # Reset to unlimited (0 means no limit)
            meter.set_quota("user1", per_day=0)
            bucket = meter.get_usage("user1")
            self.assertEqual(
                bucket.quota_tokens_per_day, 0,
                "set_quota(per_day=0) must reset to unlimited (0), not be a no-op",
            )

    def test_omitting_per_day_does_not_reset_quota(self):
        import tempfile
        from pathlib import Path
        from aictl.core.metering import TokenMeter

        with tempfile.TemporaryDirectory() as d:
            meter = TokenMeter(Path(d))
            meter.set_quota("user2", per_day=1000)
            # Update per_month only; per_day must stay at 1000
            meter.set_quota("user2", per_month=30000)
            bucket = meter.get_usage("user2")
            self.assertEqual(bucket.quota_tokens_per_day, 1000)
            self.assertEqual(bucket.quota_tokens_per_month, 30000)

    def test_set_quota_per_minute_zero_resets(self):
        import tempfile
        from pathlib import Path
        from aictl.core.metering import TokenMeter

        with tempfile.TemporaryDirectory() as d:
            meter = TokenMeter(Path(d))
            meter.set_quota("user3", per_minute=100)
            meter.set_quota("user3", per_minute=0)
            bucket = meter.get_usage("user3")
            self.assertEqual(bucket.quota_tokens_per_minute, 0)


if __name__ == "__main__":
    unittest.main()
