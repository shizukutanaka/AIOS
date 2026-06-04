"""Tests for Phase 3: Quadlet apply, config CLI, status, cache, governor, recipes."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.core.config import Config, load_config, save_config, EngineEndpoints, SLOConfig
from aictl.core.state import StateStore, NodeState
from aictl.runtime.cache import scan_cache, format_bytes, CacheEntry, find_stale
from aictl.daemon.governor import GovernorDaemon, GovernorState
from aictl.stack.manifest import get_recipe, list_recipes, _build_manifest
from aictl.stack.quadlet import generate_quadlets


class TestNewRecipes(unittest.TestCase):
    def test_all_recipes_parse(self):
        for name in list_recipes():
            m = get_recipe(name)
            self.assertIsNotNone(m, f"Recipe {name} returned None")
            self.assertTrue(len(m.services) > 0, f"Recipe {name} has no services")

    def test_recipe_count(self):
        names = list_recipes()
        self.assertGreaterEqual(len(names), 7)

    def test_code_assist_recipe(self):
        m = get_recipe("code-assist")
        self.assertIsNotNone(m)
        self.assertEqual(len(m.services), 2)
        self.assertTrue(any(s.runtime == "vllm" for s in m.services))

    def test_whisper_recipe(self):
        m = get_recipe("whisper-stt")
        self.assertIsNotNone(m)
        self.assertTrue(m.services[0].gpu_required)

    def test_embedding_only_recipe(self):
        m = get_recipe("embedding-only")
        self.assertIsNotNone(m)
        self.assertEqual(m.services[0].runtime, "ollama")

    def test_local_gpu_chat_recipe(self):
        m = get_recipe("local-gpu-chat")
        self.assertIsNotNone(m)
        vllm = [s for s in m.services if s.runtime == "vllm"]
        self.assertEqual(len(vllm), 1)


class TestQuadletApplyMode(unittest.TestCase):
    def test_quadlet_dry_run_generates_units(self):
        """apply --quadlet --dry-run should generate units without writing."""
        m = get_recipe("team-rag")
        units = generate_quadlets(m)
        self.assertGreater(len(units), 0)
        for u in units:
            self.assertTrue(u.filename.endswith(".container"))
            self.assertIn("[Container]", u.content)

    def test_code_assist_quadlet(self):
        m = get_recipe("code-assist")
        units = generate_quadlets(m)
        # Both vllm and tabby should get units
        self.assertGreaterEqual(len(units), 2)
        names = [u.filename for u in units]
        self.assertTrue(any("llm" in n for n in names))
        self.assertTrue(any("tabby" in n for n in names))

    def test_whisper_quadlet_gpu(self):
        m = get_recipe("whisper-stt")
        units = generate_quadlets(m)
        self.assertEqual(len(units), 1)
        self.assertIn("nvidia.com/gpu=all", units[0].content)

    def test_apply_parser_quadlet_flag(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["apply", "-f", "stack.json", "--quadlet", "--dry-run"])
        self.assertTrue(args.quadlet)
        self.assertTrue(args.dry_run)

    def test_apply_parser_root_flag(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["apply", "-f", "s.json", "--quadlet", "--root"])
        self.assertTrue(args.root)


class TestConfigCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_config_set_and_load(self):
        c = Config()
        c.trust_policy = "enforce"
        save_config(c, self.tmp)
        loaded = load_config(self.tmp)
        self.assertEqual(loaded.trust_policy, "enforce")

    def test_config_slo_roundtrip(self):
        c = Config()
        c.slo.ttft_p95_ms = 123.0
        c.slo.error_rate_max = 0.1
        save_config(c, self.tmp)
        loaded = load_config(self.tmp)
        self.assertEqual(loaded.slo.ttft_p95_ms, 123.0)
        self.assertEqual(loaded.slo.error_rate_max, 0.1)

    def test_config_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["config", "show"])
        self.assertEqual(args.config_cmd, "show")

    def test_config_set_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["config", "set", "trust_policy", "enforce"])
        self.assertEqual(args.key, "trust_policy")
        self.assertEqual(args.value, "enforce")


class TestStatusCommand(unittest.TestCase):
    def test_status_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["status"])
        self.assertEqual(args.command, "status")


class TestModelCache(unittest.TestCase):
    def test_format_bytes(self):
        self.assertEqual(format_bytes(0), "0.0 B")
        self.assertEqual(format_bytes(1024), "1.0 KB")
        self.assertEqual(format_bytes(1048576), "1.0 MB")
        self.assertIn("GB", format_bytes(2 * 1024**3))

    def test_scan_nonexistent_dirs(self):
        """Scanning non-existent dirs should return empty report."""
        report = scan_cache(extra_dirs=[Path("/nonexistent/path")])
        # Don't assert total is 0 because user home may have caches
        self.assertIsInstance(report.entries, list)

    def test_find_stale_empty(self):
        from aictl.runtime.cache import CacheReport
        report = CacheReport()
        stale = find_stale(report, days=30)
        self.assertEqual(len(stale), 0)

    def test_find_stale_with_old_entry(self):
        from aictl.runtime.cache import CacheReport
        old_entry = CacheEntry(
            path="/tmp/old.bin", name="old.bin", size_bytes=1000,
            last_accessed=time.time() - 90 * 86400, source="test",
        )
        report = CacheReport(entries=[old_entry])
        stale = find_stale(report, days=30)
        self.assertEqual(len(stale), 1)

    def test_model_cache_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["model", "cache", "--clean", "--days", "60"])
        self.assertEqual(args.model_cmd, "cache")
        self.assertTrue(args.clean)
        self.assertEqual(args.days, 60)


class TestGovernorDaemon(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = StateStore(self.tmp)
        self.store.save_node(NodeState(node_id="test", hostname="test"))

    def test_start_stop(self):
        gov = GovernorDaemon(self.store, interval_s=0.1)
        gov.start()
        self.assertTrue(gov.state.running)
        time.sleep(0.3)
        gov.stop()
        self.assertFalse(gov.state.running)
        self.assertGreater(gov.state.tick_count, 0)

    def test_get_status(self):
        gov = GovernorDaemon(self.store, interval_s=0.1)
        gov.start()
        time.sleep(0.3)
        status = gov.get_status()
        gov.stop()
        self.assertIn("running", status)
        self.assertIn("tick_count", status)
        self.assertGreater(status["tick_count"], 0)

    def test_governor_state_initial(self):
        state = GovernorState()
        self.assertFalse(state.running)
        self.assertEqual(state.tick_count, 0)
        self.assertEqual(state.consecutive_violations, 0)

    def test_double_start(self):
        gov = GovernorDaemon(self.store, interval_s=0.1)
        gov.start()
        gov.start()  # Should not crash
        self.assertTrue(gov.state.running)
        gov.stop()


class TestCLIIntegrationPhase3(unittest.TestCase):
    def test_all_commands_registered(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        # Commands with no required positional args
        for cmd in ["init", "doctor", "ps", "status", "serve"]:
            args = p.parse_args([cmd])
            self.assertEqual(args.command, cmd)
        # Commands with required args
        args = p.parse_args(["apply", "-f", "x"])
        self.assertEqual(args.command, "apply")
        args = p.parse_args(["logs", "svc"])
        self.assertEqual(args.command, "logs")
        args = p.parse_args(["down", "mystack"])
        self.assertEqual(args.command, "down")


if __name__ == "__main__":
    unittest.main()
