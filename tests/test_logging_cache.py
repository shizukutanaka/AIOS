"""Tests for structured logging, prefix cache analytics."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.core.logging import StructuredLogger, LogEntry, get_logger
from aictl.runtime.prefix_cache import (
    CacheStats, scrape_cache_stats, analyze_cache_efficiency,
    _cache_recommendation,
)


class TestStructuredLogger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.logger = StructuredLogger("test", self.tmp)

    def test_log_creates_file(self):
        self.logger.info("test message")
        files = list(self.tmp.glob("aictl-*.jsonl"))
        self.assertEqual(len(files), 1)

    def test_log_json_format(self):
        self.logger.info("hello", key="value", count=42)
        files = list(self.tmp.glob("aictl-*.jsonl"))
        line = files[0].read_text().strip()
        entry = json.loads(line)
        self.assertEqual(entry["level"], "info")
        self.assertEqual(entry["msg"], "hello")
        self.assertEqual(entry["key"], "value")
        self.assertEqual(entry["count"], 42)

    def test_log_levels(self):
        logger = StructuredLogger("levels", self.tmp, level="debug")
        logger.debug("d")
        logger.info("i")
        logger.warn("w")
        logger.error("e")
        files = list(self.tmp.glob("aictl-*.jsonl"))
        lines = files[0].read_text().strip().splitlines()
        self.assertEqual(len(lines), 4)

    def test_log_level_filter(self):
        logger = StructuredLogger("test", self.tmp, level="warn")
        logger.debug("skip")
        logger.info("skip")
        logger.warn("keep")
        logger.error("keep")
        files = list(self.tmp.glob("aictl-*.jsonl"))
        lines = files[0].read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)

    def test_read_logs(self):
        self.logger.info("msg1")
        self.logger.warn("msg2")
        entries = self.logger.read_logs(n=10)
        self.assertEqual(len(entries), 2)
        # Most recent first
        self.assertEqual(entries[0]["msg"], "msg2")

    def test_read_logs_level_filter(self):
        self.logger.info("a")
        self.logger.warn("b")
        self.logger.info("c")
        entries = self.logger.read_logs(n=10, level="warn")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["msg"], "b")

    def test_context(self):
        self.logger.set_context(node_id="node-1", correlation_id="req-123")
        self.logger.info("with context")
        entries = self.logger.read_logs(n=1)
        self.assertEqual(entries[0]["node_id"], "node-1")
        self.assertEqual(entries[0]["correlation_id"], "req-123")

    def test_rotate(self):
        # Create old log
        old_file = self.tmp / "aictl-2020-01-01.jsonl"
        old_file.write_text('{"msg":"old"}\n')
        import os
        os.utime(old_file, (0, 0))  # Set mtime to epoch
        removed = self.logger.rotate(max_days=1)
        self.assertEqual(removed, 1)
        self.assertFalse(old_file.exists())


class TestLogEntry(unittest.TestCase):
    def test_to_json(self):
        entry = LogEntry(level="info", logger="test", message="hello")
        j = json.loads(entry.to_json())
        self.assertEqual(j["level"], "info")
        self.assertEqual(j["msg"], "hello")
        self.assertIn("ts", j)

    def test_extra_fields(self):
        entry = LogEntry(level="info", logger="t", message="m", extra={"k": 1})
        j = json.loads(entry.to_json())
        self.assertEqual(j["k"], 1)


class TestPrefixCache(unittest.TestCase):
    def test_cache_stats_defaults(self):
        stats = CacheStats(engine="vllm")
        self.assertEqual(stats.hit_rate, 0.0)
        self.assertEqual(stats.kv_cache_usage, 0.0)

    def test_scrape_unreachable(self):
        stats = scrape_cache_stats("vllm", "http://127.0.0.1:99999")
        self.assertEqual(stats.hit_rate, 0.0)  # Should not crash

    def test_analyze_empty(self):
        result = analyze_cache_efficiency({})
        self.assertEqual(result["overall_hit_rate"], 0.0)
        self.assertEqual(result["engines"], 0)

    def test_recommendation_excellent(self):
        r = _cache_recommendation(0.85, 0.5)
        self.assertIn("Excellent", r)

    def test_recommendation_low(self):
        r = _cache_recommendation(0.1, 0.3)
        self.assertIn("Low", r)

    def test_recommendation_no_data(self):
        r = _cache_recommendation(0.0, 0.0)
        self.assertIn("No cache data", r)

    def test_recommendation_capacity(self):
        r = _cache_recommendation(0.6, 0.95)
        self.assertIn("capacity", r)


class TestLogCLI(unittest.TestCase):
    def test_log_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["log", "-n", "50", "--level", "error"])
        self.assertEqual(args.n, 50)
        self.assertEqual(args.level, "error")

    def test_gate_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["gate", "--skip-demo"])
        self.assertTrue(args.skip_demo)


if __name__ == "__main__":
    unittest.main()
