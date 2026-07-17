"""Coverage tests for cmd modules (batch 4).

Exercises cluster, init, upgrade, apply, fabric, bench, and registration
tests for server-loop modules (chat, serve, proxy) that cannot be driven
end-to-end without blocking.
"""

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _ns(**kw):
    return argparse.Namespace(**kw)


def _capture(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(args)
    return rc, buf.getvalue()


def _capture_json(fn, args):
    rc, out = _capture(fn, args)
    out = out.strip()
    return rc, (json.loads(out) if out else None)


def _extract_last_json(out: str) -> dict | None:
    """Find the last '{' in output and parse from there (handles ok() prefix lines)."""
    idx = out.rfind("{")
    if idx < 0:
        return None
    return json.loads(out[idx:])


# ─── cluster ──────────────────────────────────────────────

class TestClusterCmd(unittest.TestCase):
    def test_promote_json_returns_plan(self):
        from aictl.cmd import cluster
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                cluster.run_promote, _ns(state_dir=Path(d), dry_run=True, json=True))
        self.assertEqual(rc, 0)
        self.assertIn("ready", parsed)
        self.assertIn("steps", parsed)
        self.assertIn("reason", parsed)

    def test_promote_steps_are_ordered(self):
        from aictl.cmd import cluster
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                cluster.run_promote, _ns(state_dir=Path(d), dry_run=True, json=True))
        self.assertEqual(rc, 0)
        steps = parsed["steps"]
        if steps:
            orders = [s["order"] for s in steps]
            self.assertEqual(orders, sorted(orders))

    def test_export_known_recipe_returns_k8s_list(self):
        from aictl.cmd import cluster
        from aictl.stack.manifest import list_recipes
        names = list_recipes()
        if not names:
            self.skipTest("no recipes available")
        rc, out = _capture(cluster.run_export, _ns(state_dir=None, stack=names[0], json=False))
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual(doc["kind"], "List")
        self.assertIn("items", doc)

    def test_export_unknown_stack_returns_1(self):
        from aictl.cmd import cluster
        rc, _ = _capture(cluster.run_export, _ns(state_dir=None, stack="no-such-xyz", json=False))
        self.assertEqual(rc, 1)


# ─── init ─────────────────────────────────────────────────

class TestInitCmd(unittest.TestCase):
    def test_fresh_init_json_returns_node(self):
        from aictl.cmd import init as init_cmd
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                init_cmd.run, _ns(state_dir=Path(d), force=False, json=True))
        self.assertEqual(rc, 0)
        self.assertIsNotNone(parsed)
        self.assertIn("node_id", parsed)
        self.assertTrue(len(parsed["node_id"]) > 0)

    def test_reinit_without_force_returns_1(self):
        from aictl.cmd import init as init_cmd
        with tempfile.TemporaryDirectory() as d:
            # First init
            _capture(init_cmd.run, _ns(state_dir=Path(d), force=False, json=True))
            # Second init without --force should fail
            rc, _ = _capture(init_cmd.run, _ns(state_dir=Path(d), force=False, json=False))
        self.assertEqual(rc, 1)

    def test_reinit_with_force_succeeds(self):
        from aictl.cmd import init as init_cmd
        with tempfile.TemporaryDirectory() as d:
            _capture(init_cmd.run, _ns(state_dir=Path(d), force=False, json=True))
            rc, parsed = _capture_json(
                init_cmd.run, _ns(state_dir=Path(d), force=True, json=True))
        self.assertEqual(rc, 0)
        self.assertIn("node_id", parsed)

    def test_each_init_generates_unique_node_id(self):
        from aictl.cmd import init as init_cmd
        ids = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as d:
                _, parsed = _capture_json(
                    init_cmd.run, _ns(state_dir=Path(d), force=False, json=True))
                ids.append(parsed["node_id"])
        self.assertNotEqual(ids[0], ids[1])


# ─── upgrade ──────────────────────────────────────────────

class TestUpgradeCmd(unittest.TestCase):
    def test_plan_json_has_required_fields(self):
        from aictl.cmd import upgrade
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                upgrade.run_plan,
                _ns(state_dir=Path(d), target_version="v1.7.0", json=True),
            )
        self.assertEqual(rc, 0)
        for field in ("current_version", "target_version", "steps", "rollback"):
            self.assertIn(field, parsed)

    def test_plan_target_version_reflected(self):
        from aictl.cmd import upgrade
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                upgrade.run_plan,
                _ns(state_dir=Path(d), target_version="v2.0.0", json=True),
            )
        self.assertEqual(rc, 0)
        self.assertEqual(parsed["target_version"], "v2.0.0")

    def test_plan_steps_include_snapshot(self):
        from aictl.cmd import upgrade
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                upgrade.run_plan,
                _ns(state_dir=Path(d), target_version="next", json=True),
            )
        self.assertEqual(rc, 0)
        actions = [s["action"] for s in parsed["steps"]]
        self.assertIn("snapshot_state", actions)


# ─── apply ────────────────────────────────────────────────

class TestApplyCmd(unittest.TestCase):
    def test_dry_run_with_example_manifest_json(self):
        from aictl.cmd import apply
        manifest_path = EXAMPLES_DIR / "stack.local-rag.yaml"
        if not manifest_path.exists():
            self.skipTest("example manifest not found")
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                apply.run,
                _ns(state_dir=Path(d), file=str(manifest_path),
                    dry_run=True, quadlet=False, root=False, json=True),
            )
        self.assertEqual(rc, 0)
        self.assertIn("stack", parsed)
        self.assertIn("services", parsed)
        self.assertIsInstance(parsed["services"], list)

    def test_missing_manifest_returns_1(self):
        from aictl.cmd import apply
        with tempfile.TemporaryDirectory() as d:
            rc, _ = _capture(
                apply.run,
                _ns(state_dir=Path(d), file="/nonexistent/stack.yaml",
                    dry_run=True, quadlet=False, root=False, json=False),
            )
        self.assertEqual(rc, 1)

    def test_dry_run_does_not_persist_stack(self):
        from aictl.cmd import apply
        from aictl.core.state import StateStore
        manifest_path = EXAMPLES_DIR / "stack.local-rag.yaml"
        if not manifest_path.exists():
            self.skipTest("example manifest not found")
        with tempfile.TemporaryDirectory() as d:
            _capture(
                apply.run,
                _ns(state_dir=Path(d), file=str(manifest_path),
                    dry_run=True, quadlet=False, root=False, json=True),
            )
            store = StateStore(Path(d))
            stacks = store.load_stacks()
        self.assertEqual(stacks, [])


# ─── fabric ───────────────────────────────────────────────

class TestFabricCmd(unittest.TestCase):
    def test_detect_json_has_tiers(self):
        from aictl.cmd import fabric
        rc, parsed = _capture_json(fabric.run_detect, _ns(json=True))
        self.assertEqual(rc, 0)
        self.assertIn("tiers", parsed)
        self.assertIsInstance(parsed["tiers"], list)
        self.assertGreater(len(parsed["tiers"]), 0)

    def test_detect_tiers_have_required_fields(self):
        from aictl.cmd import fabric
        rc, parsed = _capture_json(fabric.run_detect, _ns(json=True))
        self.assertEqual(rc, 0)
        for tier in parsed["tiers"]:
            self.assertIn("name", tier)
            self.assertIn("capacity_gb", tier)

    def test_policy_json_has_placement_keys(self):
        from aictl.cmd import fabric
        rc, parsed = _capture_json(fabric.run_policy, _ns(json=True))
        self.assertEqual(rc, 0)
        self.assertIsNotNone(parsed)
        # Policy must describe where to place model weights
        self.assertIn("model_weights", parsed)


# ─── bench ────────────────────────────────────────────────

class TestBenchCmd(unittest.TestCase):
    def test_mock_benchmark_json_result(self):
        # Use a single mock bench test to avoid port reuse conflicts across tests.
        from aictl.cmd import bench
        rc, out = _capture(
            bench.run, _ns(endpoint="", model="", requests=2, max_tokens=20,
                           mock=True, json=True))
        self.assertEqual(rc, 0)
        parsed = _extract_last_json(out)
        self.assertIsNotNone(parsed)
        self.assertIn("requests", parsed)
        self.assertIn("errors", parsed)
        self.assertIn("tokens_per_sec", parsed)
        self.assertEqual(parsed["requests"], 2)
        self.assertEqual(parsed["errors"], 0)

    def test_unreachable_endpoint_returns_1(self):
        # json=False so the all-errors branch can return 1 (json path always returns 0)
        from aictl.cmd import bench
        rc, _ = _capture(
            bench.run, _ns(endpoint="http://127.0.0.1:1", model="m",
                           requests=1, max_tokens=10, mock=False, json=False))
        self.assertEqual(rc, 1)


# ─── server-loop module imports ───────────────────────────

class TestServerModuleRegistration(unittest.TestCase):
    """Verify that blocking-loop modules import cleanly and register correctly."""

    def _check_register(self, mod_name: str):
        import importlib, argparse
        mod = importlib.import_module(f"aictl.cmd.{mod_name}")
        self.assertTrue(callable(mod.register), f"{mod_name}.register must be callable")
        # register() should not crash when given a real subparsers object
        p = argparse.ArgumentParser()
        sub = p.add_subparsers()
        mod.register(sub)  # must not raise

    def test_chat_registers(self):
        self._check_register("chat")

    def test_serve_registers(self):
        self._check_register("serve")

    def test_proxy_registers(self):
        self._check_register("proxy")

    def test_gate_registers(self):
        self._check_register("gate")

    def test_selftest_registers(self):
        self._check_register("selftest")


if __name__ == "__main__":
    unittest.main()
