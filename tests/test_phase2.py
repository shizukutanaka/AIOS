"""Tests for Phase 2: router, adapters, config, nodes, governor."""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.adapters import (
    EngineHealth, VLLMAdapter, OllamaAdapter, SGLangAdapter,
    _prom_gauge, _prom_histogram_quantile, discover_engines,
)
from aictl.runtime.router import (
    BrokerRouter, RouteRequest, RouteDecision, EngineCandidate,
    SLOGovernor, WEIGHTS, ENGINE_COST,
)
from aictl.runtime.nodes import NodeManager, PeerNode, ClusterState
from aictl.core.config import Config, load_config, save_config, EngineEndpoints, SLOConfig
from aictl.core.state import StateStore, NodeState
from aictl.metrics.slo import InferenceMetrics, SLOTarget


# ── Prometheus parsing ──────────────────────────────────

class TestPromParsing(unittest.TestCase):
    SAMPLE_METRICS = """
# HELP vllm:num_requests_waiting Number of requests waiting
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting 5
# HELP vllm:num_requests_running Number of requests running
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running 3
# HELP vllm:gpu_cache_usage_perc GPU cache usage
# TYPE vllm:gpu_cache_usage_perc gauge
vllm:gpu_cache_usage_perc 0.75
# HELP vllm:avg_generation_throughput_toks_per_s Throughput
# TYPE vllm:avg_generation_throughput_toks_per_s gauge
vllm:avg_generation_throughput_toks_per_s 42.5
# HELP vllm:time_to_first_token_seconds_sum TTFT sum
vllm:time_to_first_token_seconds_sum 15.0
vllm:time_to_first_token_seconds_count 100
"""

    def test_gauge_simple(self):
        self.assertEqual(_prom_gauge(self.SAMPLE_METRICS, "vllm:num_requests_waiting"), 5.0)

    def test_gauge_float(self):
        self.assertAlmostEqual(_prom_gauge(self.SAMPLE_METRICS, "vllm:gpu_cache_usage_perc"), 0.75)

    def test_gauge_missing(self):
        self.assertEqual(_prom_gauge(self.SAMPLE_METRICS, "nonexistent"), 0.0)

    def test_histogram_fallback_avg(self):
        """When no quantile is pre-computed, fall back to sum/count."""
        val = _prom_histogram_quantile(self.SAMPLE_METRICS, "vllm:time_to_first_token_seconds", 0.95)
        self.assertAlmostEqual(val, 0.15, places=2)  # 15/100

    def test_gauge_throughput(self):
        self.assertAlmostEqual(
            _prom_gauge(self.SAMPLE_METRICS, "vllm:avg_generation_throughput_toks_per_s"), 42.5
        )


# ── Broker Router ───────────────────────────────────────

class TestBrokerRouter(unittest.TestCase):
    def _make_health(self, engine, reachable=True, status="READY", models=None):
        return EngineHealth(
            engine=engine, endpoint=f"http://localhost:0",
            reachable=reachable, status=status, models=models or [],
        )

    def test_hard_filter_unreachable(self):
        router = BrokerRouter()
        c = EngineCandidate("vllm", "http://x", self._make_health("vllm", reachable=False))
        self.assertEqual(router._hard_filter(c, RouteRequest(model="m")), "unreachable")

    def test_hard_filter_offline(self):
        router = BrokerRouter()
        c = EngineCandidate("vllm", "http://x", self._make_health("vllm", status="OFFLINE"))
        self.assertIn("status=", router._hard_filter(c, RouteRequest(model="m")))

    def test_hard_filter_model_missing(self):
        router = BrokerRouter()
        h = self._make_health("vllm", models=["llama3"])
        c = EngineCandidate("vllm", "http://x", h)
        result = router._hard_filter(c, RouteRequest(model="mistral"))
        self.assertIn("not loaded", result)

    def test_hard_filter_model_match(self):
        router = BrokerRouter()
        h = self._make_health("vllm", models=["meta-llama/Llama-3.2-8B"])
        c = EngineCandidate("vllm", "http://x", h)
        result = router._hard_filter(c, RouteRequest(model="Llama-3.2"))
        self.assertEqual(result, "")

    def test_hard_filter_ready_passes(self):
        router = BrokerRouter()
        c = EngineCandidate("vllm", "http://x", self._make_health("vllm"))
        self.assertEqual(router._hard_filter(c, RouteRequest(model="")), "")

    def test_soft_score_range(self):
        router = BrokerRouter()
        h = self._make_health("vllm")
        c = EngineCandidate("vllm", "http://x", h)
        c.metrics = InferenceMetrics(ttft_ms_p95=200, kv_cache_utilization=0.3)
        score = router._soft_score(c, RouteRequest(model="m", objective="balanced"))
        self.assertGreater(score, 0)
        self.assertLessEqual(score, 1.0)

    def test_soft_score_degraded_penalty(self):
        router = BrokerRouter()
        h_ready = self._make_health("vllm", status="READY")
        h_degraded = self._make_health("vllm", status="DEGRADED")
        c1 = EngineCandidate("vllm", "http://x", h_ready, InferenceMetrics())
        c2 = EngineCandidate("vllm", "http://x", h_degraded, InferenceMetrics())
        s1 = router._soft_score(c1, RouteRequest(model="m"))
        s2 = router._soft_score(c2, RouteRequest(model="m"))
        self.assertGreater(s1, s2)

    def test_weights_all_objectives(self):
        for obj in ("latency", "throughput", "cost", "balanced"):
            w = WEIGHTS[obj]
            total = sum(w.values())
            self.assertAlmostEqual(total, 1.0, places=2, msg=f"{obj} weights don't sum to 1")

    def test_engine_cost_values(self):
        for engine, cost in ENGINE_COST.items():
            self.assertGreaterEqual(cost, 0)
            self.assertLessEqual(cost, 1)


# ── Config ──────────────────────────────────────────────

class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_default_config(self):
        c = load_config(self.tmp)
        self.assertEqual(c.engines.vllm, "http://localhost:8000")
        self.assertEqual(c.trust_policy, "warn")

    def test_save_load_roundtrip(self):
        c = Config()
        c.engines.vllm = "http://custom:9000"
        c.trust_policy = "enforce"
        c.slo.ttft_p95_ms = 200.0
        save_config(c, self.tmp)
        loaded = load_config(self.tmp)
        self.assertEqual(loaded.engines.vllm, "http://custom:9000")
        self.assertEqual(loaded.trust_policy, "enforce")
        self.assertEqual(loaded.slo.ttft_p95_ms, 200.0)

    def test_engine_endpoints_to_dict(self):
        e = EngineEndpoints()
        d = e.to_dict()
        self.assertIn("vllm", d)
        self.assertIn("ollama", d)
        self.assertIn("sglang", d)


# ── Node Manager ────────────────────────────────────────

class TestNodeManager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = StateStore(self.tmp)
        self.store.save_node(NodeState(node_id="test123", hostname="test"))

    def test_generate_token(self):
        mgr = NodeManager(self.store)
        token = mgr.generate_join_token()
        self.assertTrue(len(token) > 20)
        cs = mgr.load_cluster()
        self.assertEqual(cs.join_token, token)

    def test_accept_join_valid_token(self):
        mgr = NodeManager(self.store)
        token = mgr.generate_join_token()
        result = mgr.accept_join({
            "node_id": "remote1", "hostname": "remote",
            "address": "192.168.1.100", "port": 7700, "token": token,
        })
        self.assertTrue(result["accepted"])
        cs = mgr.load_cluster()
        self.assertEqual(len(cs.peers), 1)
        self.assertEqual(cs.mode, "cluster")

    def test_accept_join_invalid_token(self):
        mgr = NodeManager(self.store)
        mgr.generate_join_token()
        result = mgr.accept_join({"token": "wrong-token"})
        self.assertFalse(result["accepted"])

    def test_should_promote_no_peers(self):
        mgr = NodeManager(self.store)
        promote, _ = mgr.should_promote_to_k3s()
        self.assertFalse(promote)

    def test_should_promote_with_active_peer(self):
        mgr = NodeManager(self.store)
        token = mgr.generate_join_token()
        mgr.accept_join({
            "node_id": "r1", "hostname": "r", "address": "10.0.0.2",
            "port": 7700, "token": token,
        })
        promote, reason = mgr.should_promote_to_k3s()
        self.assertTrue(promote)
        self.assertIn("K3s", reason)

    def test_cluster_persistence(self):
        mgr = NodeManager(self.store)
        token = mgr.generate_join_token()
        mgr.accept_join({"node_id": "r1", "hostname": "r", "address": "10.0.0.2",
                         "port": 7700, "token": token})
        # Reload
        mgr2 = NodeManager(self.store)
        cs = mgr2.load_cluster()
        self.assertEqual(len(cs.peers), 1)
        self.assertEqual(cs.peers[0].node_id, "r1")


# ── SLO Governor ────────────────────────────────────────

class TestSLOGovernor(unittest.TestCase):
    def test_governor_tick_no_engines(self):
        """Governor should return 'none' action when no engines are reachable."""
        router = BrokerRouter(endpoints={"vllm": "http://localhost:99999"})
        gov = SLOGovernor(router)
        action = gov.tick()
        self.assertEqual(action.action, "none")

    def test_recent_actions_empty(self):
        router = BrokerRouter(endpoints={})
        gov = SLOGovernor(router)
        self.assertEqual(gov.recent_actions(), [])

    def test_history_limit(self):
        router = BrokerRouter(endpoints={})
        gov = SLOGovernor(router)
        gov._max_history = 5
        for _ in range(10):
            gov.tick()
        self.assertLessEqual(len(gov.history), 5)


# ── Extended Daemon API ─────────────────────────────────

class TestDaemonBrokerAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from aictl.daemon.aiosd import AIOSHandler, DEFAULT_HOST
        from http.server import HTTPServer

        cls.tmp = tempfile.mkdtemp()
        store = StateStore(Path(cls.tmp))
        store.save_node(NodeState(node_id="daemon-test", hostname="test"))
        AIOSHandler.store = store

        cls.port = 17701
        cls.server = HTTPServer((DEFAULT_HOST, cls.port), AIOSHandler)
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
            return json.loads(r.read())

    def _post(self, path, body):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                    headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_broker_engines(self):
        data = self._get("/v1/broker/engines")
        self.assertIn("engines", data)
        self.assertIsInstance(data["engines"], list)

    def test_broker_route(self):
        data = self._post("/v1/broker/route", {"model": "llama3", "objective": "latency"})
        self.assertIn("reason_codes", data)
        self.assertIn("latency_ms", data)

    def test_broker_failover(self):
        data = self._post("/v1/broker/failover", {"request_id": "r1", "last_error": "timeout"})
        self.assertIn("fallback_target", data)

    def test_broker_drain(self):
        data = self._post("/v1/broker/drain", {"target": "vllm"})
        self.assertIn("status", data)

    def test_broker_governor(self):
        data = self._get("/v1/broker/governor")
        self.assertIn("action", data)

    def test_cluster_status(self):
        data = self._get("/v1/cluster")
        self.assertIn("mode", data)

    def test_node_join_no_token(self):
        """Join without valid token should fail."""
        try:
            self._post("/v1/node/join", {"token": "invalid"})
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)


# ── CLI Integration ─────────────────────────────────────

class TestCLINewCommands(unittest.TestCase):
    def test_node_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["node", "token"])
        self.assertEqual(args.command, "node")
        self.assertEqual(args.node_cmd, "token")

    def test_node_pair_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["node", "pair", "10.0.0.1", "--token", "abc"])
        self.assertEqual(args.address, "10.0.0.1")
        self.assertEqual(args.token, "abc")

    def test_logs_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["logs", "my-service", "-f", "-n", "100"])
        self.assertEqual(args.service, "my-service")
        self.assertTrue(args.follow)
        self.assertEqual(args.tail, "100")


if __name__ == "__main__":
    unittest.main()
