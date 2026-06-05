"""Tests for Phase 7: Prometheus metrics, event bus, proxy, warmup, net."""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.core.events import EventBus, Event, emit, get_bus, STACK_APPLIED, SLO_VIOLATION
from aictl.core.state import StateStore, NodeState
from aictl.metrics.prometheus import generate_metrics_text
from aictl.runtime.warmup import WarmupManager, UsageRecord


class TestEventBus(unittest.TestCase):
    def test_publish_subscribe(self):
        bus = EventBus()
        received = []
        bus.subscribe("test.event", lambda e: received.append(e))
        bus.publish(Event(type="test.event", data={"key": "val"}))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["key"], "val")

    def test_subscribe_all(self):
        bus = EventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.type))
        bus.publish(Event(type="a"))
        bus.publish(Event(type="b"))
        self.assertEqual(received, ["a", "b"])

    def test_no_cross_contamination(self):
        bus = EventBus()
        received = []
        bus.subscribe("x", lambda e: received.append(e))
        bus.publish(Event(type="y"))
        self.assertEqual(len(received), 0)

    def test_recent(self):
        bus = EventBus()
        for i in range(10):
            bus.publish(Event(type=f"e{i}"))
        recent = bus.recent(3)
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[-1].type, "e9")

    def test_recent_filtered(self):
        bus = EventBus()
        bus.publish(Event(type="a"))
        bus.publish(Event(type="b"))
        bus.publish(Event(type="a"))
        recent = bus.recent(10, event_type="a")
        self.assertEqual(len(recent), 2)

    def test_history_limit(self):
        bus = EventBus(max_history=5)
        for i in range(10):
            bus.publish(Event(type=f"e{i}"))
        self.assertLessEqual(len(bus._history), 5)

    def test_clear(self):
        bus = EventBus()
        bus.publish(Event(type="x"))
        bus.clear()
        self.assertEqual(len(bus.recent()), 0)

    def test_thread_safety(self):
        bus = EventBus()
        results = []

        def publisher():
            for i in range(100):
                bus.publish(Event(type=f"t{i}"))

        threads = [threading.Thread(target=publisher) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertGreaterEqual(len(bus._history), 400)

    def test_global_emit(self):
        bus = get_bus()
        bus.clear()
        emit(STACK_APPLIED, source="test", name="test-stack")
        recent = bus.recent(1)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].type, STACK_APPLIED)


class TestPrometheusMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = StateStore(self.tmp)
        self.store.save_node(NodeState(
            node_id="prom-test", hostname="test",
            profile="cpu-only", version="0.7.0",
            gpu_count=0, vram_total_mb=0, ram_total_mb=16384,
        ))

    def test_generates_text(self):
        text = generate_metrics_text(self.store)
        self.assertIn("# HELP", text)
        self.assertIn("# TYPE", text)
        self.assertIn("aios_node_info", text)

    def test_contains_psi_metrics(self):
        text = generate_metrics_text(self.store)
        self.assertIn("aios_psi_memory_some_avg10", text)
        self.assertIn("aios_psi_cpu_some_avg10", text)

    def test_contains_node_info(self):
        text = generate_metrics_text(self.store)
        self.assertIn('node_id="prom-test"', text)
        self.assertIn('profile="cpu-only"', text)

    def test_contains_engine_metrics(self):
        text = generate_metrics_text(self.store)
        self.assertIn("aios_engine_reachable", text)

    def test_valid_prometheus_format(self):
        text = generate_metrics_text(self.store)
        for line in text.strip().splitlines():
            if line.startswith("#"):
                self.assertTrue(line.startswith("# HELP") or line.startswith("# TYPE"))
            elif line.strip():
                # metric_name{labels} value  OR  metric_name value
                parts = line.split()
                self.assertGreaterEqual(len(parts), 2, f"Bad line: {line}")


class TestWarmupManager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = StateStore(self.tmp)

    def test_record_and_retrieve(self):
        mgr = WarmupManager(self.store)
        mgr.record_use("llama3:8b", "ollama", load_time_ms=500)
        mgr.record_use("llama3:8b", "ollama", load_time_ms=300)
        mgr.record_use("qwen2.5:7b", "ollama", load_time_ms=400)

        candidates = mgr.get_warmup_candidates(top_n=5)
        self.assertGreater(len(candidates), 0)
        # llama3 used twice, should be ranked higher
        self.assertEqual(candidates[0].model, "llama3:8b")
        self.assertEqual(candidates[0].count, 2)

    def test_empty_history(self):
        mgr = WarmupManager(self.store)
        candidates = mgr.get_warmup_candidates()
        self.assertEqual(len(candidates), 0)

    def test_avg_load_time(self):
        mgr = WarmupManager(self.store)
        mgr.record_use("m1", "ollama", load_time_ms=100)
        mgr.record_use("m1", "ollama", load_time_ms=300)
        candidates = mgr.get_warmup_candidates()
        self.assertAlmostEqual(candidates[0].avg_load_time_ms, 200, delta=1)


class TestDaemonMetricsEndpoint(unittest.TestCase):
    """Test /metrics endpoint on the daemon."""

    @classmethod
    def setUpClass(cls):
        from aictl.daemon.aiosd import AIOSHandler
        from http.server import HTTPServer

        cls.tmp = tempfile.mkdtemp()
        store = StateStore(Path(cls.tmp))
        store.save_node(NodeState(node_id="dm", hostname="test", profile="cpu-only"))
        AIOSHandler.store = store

        cls.port = 17712
        cls.server = HTTPServer(("127.0.0.1", cls.port), AIOSHandler)
        cls.server._start_time = time.time()
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.read().decode(), r.headers.get("Content-Type", "")

    def test_prometheus_metrics(self):
        body, ct = self._get("/metrics")
        self.assertIn("text/plain", ct)
        self.assertIn("aios_node_info", body)
        self.assertIn("# TYPE", body)

    def test_events_endpoint(self):
        body, _ = self._get("/v1/events")
        data = json.loads(body)
        self.assertIn("events", data)
        self.assertIsInstance(data["events"], list)


class TestNewCLICommands(unittest.TestCase):
    def test_proxy_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["proxy", "--port", "9090"])
        self.assertEqual(args.command, "proxy")
        self.assertEqual(args.port, 9090)

    def test_warmup_stats_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["warmup", "stats"])
        self.assertEqual(args.warmup_cmd, "stats")

    def test_net_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["net"])
        self.assertEqual(args.command, "net")

    def test_watch_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["watch", "--interval", "10"])
        self.assertEqual(args.interval, 10)

    def test_all_23_commands(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        simple = ["init", "doctor", "ps", "serve", "status", "setup",
                   "recommend", "proxy", "net", "watch"]
        for cmd in simple:
            args = p.parse_args([cmd])
            self.assertEqual(args.command, cmd, f"Failed: {cmd}")


if __name__ == "__main__":
    unittest.main()
