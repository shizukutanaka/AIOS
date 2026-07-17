"""Pass 75 regression tests: apikey inspect/rotate, isolation command, lora lifecycle."""

from __future__ import annotations

import argparse
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock


# ── apikey inspect / rotate ───────────────────────────────────────────────────

class TestApikeyInspect(unittest.TestCase):
    """apikey inspect — show full metadata for a key."""

    def _make_parser(self):
        from aictl.cmd.apikey import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_inspect_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["apikey", "inspect", "key123"])
        self.assertEqual(args.func.__name__, "run_inspect")
        self.assertEqual(args.key_id, "key123")

    def test_inspect_json_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["apikey", "inspect", "key123", "--json"])
        self.assertTrue(args.json)

    def test_inspect_found_json(self):
        from aictl.cmd.apikey import run_inspect
        key_data = {
            "key_id": "key_abc123",
            "name": "test-key",
            "active": True,
            "rate_limit_rpm": 60,
            "total_requests": 42,
            "total_tokens": 1234,
            "created_at": time.time(),
            "expires_at": 0,
        }
        captured = []
        with patch("aictl.cmd.apikey.KeyManager") as MockMgr:
            MockMgr.return_value.list_keys.return_value = [key_data]
            with patch("aictl.cmd.apikey.print_json", side_effect=captured.append):
                args = argparse.Namespace(key_id="key_abc123", state_dir=None, json=True)
                ret = run_inspect(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["key_id"], "key_abc123")
        self.assertEqual(captured[0]["total_requests"], 42)

    def test_inspect_prefix_match(self):
        from aictl.cmd.apikey import run_inspect
        key_data = {
            "key_id": "key_abc123full",
            "name": "prefixed-key",
            "active": True,
            "rate_limit_rpm": 30,
            "total_requests": 0,
            "total_tokens": 0,
            "created_at": time.time(),
            "expires_at": 0,
        }
        captured = []
        with patch("aictl.cmd.apikey.KeyManager") as MockMgr:
            MockMgr.return_value.list_keys.return_value = [key_data]
            with patch("aictl.cmd.apikey.print_json", side_effect=captured.append):
                args = argparse.Namespace(key_id="key_abc", state_dir=None, json=True)
                ret = run_inspect(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["name"], "prefixed-key")

    def test_inspect_not_found_returns_1(self):
        from aictl.cmd.apikey import run_inspect
        with patch("aictl.cmd.apikey.KeyManager") as MockMgr:
            MockMgr.return_value.list_keys.return_value = []
            args = argparse.Namespace(key_id="nonexistent", state_dir=None, json=False)
            ret = run_inspect(args)
        self.assertEqual(ret, 1)


class TestApikeyRotate(unittest.TestCase):
    """apikey rotate — revoke + recreate with same settings."""

    def _make_parser(self):
        from aictl.cmd.apikey import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_rotate_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["apikey", "rotate", "key123"])
        self.assertEqual(args.func.__name__, "run_rotate")

    def test_rotate_success_json(self):
        from aictl.cmd.apikey import run_rotate
        from unittest.mock import MagicMock
        key_data = {
            "key_id": "old_key_id",
            "name": "my-key",
            "rate_limit_rpm": 120,
            "expires_at": 0,
        }
        new_key_mock = MagicMock()
        new_key_mock.key_id = "new_key_id"
        captured = []
        with patch("aictl.cmd.apikey.KeyManager") as MockMgr:
            mgr_inst = MockMgr.return_value
            mgr_inst.list_keys.return_value = [key_data]
            mgr_inst.revoke.return_value = True
            mgr_inst.generate_key.return_value = ("raw_new_key", new_key_mock)
            with patch("aictl.cmd.apikey.print_json", side_effect=captured.append):
                args = argparse.Namespace(key_id="old_key_id", state_dir=None, json=True)
                ret = run_rotate(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["rotated"])
        self.assertEqual(captured[0]["old_key_id"], "old_key_id")
        self.assertEqual(captured[0]["new_key_id"], "new_key_id")
        self.assertEqual(captured[0]["new_key"], "raw_new_key")
        mgr_inst.revoke.assert_called_once_with("old_key_id")

    def test_rotate_preserves_rpm(self):
        from aictl.cmd.apikey import run_rotate
        key_data = {
            "key_id": "k1",
            "name": "rpm-key",
            "rate_limit_rpm": 200,
            "expires_at": 0,
        }
        new_key_mock = MagicMock()
        new_key_mock.key_id = "k2"
        with patch("aictl.cmd.apikey.KeyManager") as MockMgr:
            mgr_inst = MockMgr.return_value
            mgr_inst.list_keys.return_value = [key_data]
            mgr_inst.revoke.return_value = True
            mgr_inst.generate_key.return_value = ("raw", new_key_mock)
            args = argparse.Namespace(key_id="k1", state_dir=None, json=False)
            run_rotate(args)
            _, kwargs = mgr_inst.generate_key.call_args
            self.assertEqual(kwargs.get("rate_limit_rpm", kwargs.get("rate_limit_rpm")), 200)

    def test_rotate_not_found_returns_1(self):
        from aictl.cmd.apikey import run_rotate
        with patch("aictl.cmd.apikey.KeyManager") as MockMgr:
            MockMgr.return_value.list_keys.return_value = []
            args = argparse.Namespace(key_id="ghost", state_dir=None, json=False)
            ret = run_rotate(args)
        self.assertEqual(ret, 1)


# ── isolation command ─────────────────────────────────────────────────────────

class TestIsolationCommand(unittest.TestCase):
    """aictl isolation — cgroup v2 isolation configuration."""

    def _make_parser(self):
        from aictl.cmd.isolation import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_detect_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["isolation", "detect"])
        self.assertEqual(args.func.__name__, "run_detect")

    def test_config_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["isolation", "config", "llama3"])
        self.assertEqual(args.func.__name__, "run_config")
        self.assertEqual(args.model, "llama3")

    def test_apply_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["isolation", "apply", "llama3"])
        self.assertEqual(args.func.__name__, "run_apply")

    def test_apply_output_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["isolation", "apply", "llama3", "--output", "/tmp/test.slice"])
        self.assertEqual(args.output, "/tmp/test.slice")

    def test_run_detect_json(self):
        from aictl.cmd.isolation import run_detect
        fake_features = {
            "cgroup_v2": True,
            "cpuset": True,
            "memory_min": False,
            "numa": False,
            "isolcpus": False,
        }
        captured = []
        with patch("aictl.cmd.isolation.detect_cpu_isolation_support", return_value=fake_features):
            with patch("aictl.cmd.isolation.print_json", side_effect=captured.append):
                args = argparse.Namespace(json=True)
                ret = run_detect(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["cgroup_v2"], True)
        self.assertIn("cpuset", captured[0])

    def test_run_config_json(self):
        from aictl.cmd.isolation import run_config
        captured = []
        with patch("aictl.cmd.isolation.print_json", side_effect=captured.append):
            args = argparse.Namespace(model="llama3:8b", params=7.0, vram=24, ram=32, json=True)
            ret = run_config(args)
        self.assertEqual(ret, 0)
        d = captured[0]
        self.assertEqual(d["name"], "llama3-8b")  # : → - normalised by isolation module
        self.assertGreater(d["memory_min_gb"], 0)
        self.assertGreater(d["memory_max_gb"], d["memory_min_gb"])

    def test_run_config_cpu_mode(self):
        from aictl.cmd.isolation import run_config
        captured = []
        with patch("aictl.cmd.isolation.print_json", side_effect=captured.append):
            args = argparse.Namespace(model="llama3:70b", params=70.0, vram=0, ram=128, json=True)
            ret = run_config(args)
        self.assertEqual(ret, 0)
        # CPU mode: model fully in RAM → larger memory_min
        self.assertGreater(captured[0]["memory_min_gb"], 10.0)

    def test_run_apply_writes_file(self):
        from aictl.cmd.isolation import run_apply
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        out = str(tmpdir / "test.slice")
        args = argparse.Namespace(model="llama3", params=7.0, vram=0, output=out, json=False)
        ret = run_apply(args)
        self.assertEqual(ret, 0)
        content = pathlib.Path(out).read_text()
        self.assertIn("[Slice]", content)
        self.assertIn("MemoryMin", content)

    def test_run_apply_stdout(self, capsys=None):
        from aictl.cmd.isolation import run_apply
        import io, sys
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            args = argparse.Namespace(model="mymodel", params=7.0, vram=0, output="", json=False)
            ret = run_apply(args)
        finally:
            sys.stdout = old
        self.assertEqual(ret, 0)
        self.assertIn("[Slice]", buf.getvalue())

    def test_isolation_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["isolation", "detect"])
        self.assertEqual(args.func.__name__, "run_detect")


# ── lora lifecycle (inspect / delete / activate / deactivate) ────────────────

class TestLoraLifecycle(unittest.TestCase):
    """lora inspect/delete/activate/deactivate — adapter lifecycle."""

    def _make_parser(self):
        from aictl.cmd.lora import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_inspect_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["lora", "inspect", "finance-lora"])
        self.assertEqual(args.func.__name__, "run_inspect")
        self.assertEqual(args.name, "finance-lora")

    def test_delete_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["lora", "delete", "finance-lora"])
        self.assertEqual(args.func.__name__, "run_delete")

    def test_activate_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["lora", "activate", "finance-lora"])
        self.assertEqual(args.func.__name__, "run_activate")

    def test_deactivate_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["lora", "deactivate", "finance-lora"])
        self.assertEqual(args.func.__name__, "run_deactivate")

    def _make_registry(self, tmpdir: pathlib.Path):
        data = {
            "bases": {},
            "adapters": {
                "finance-lora": {
                    "name": "finance-lora",
                    "base_model": "llama3",
                    "path": "/data/finance",
                    "vram_overhead_mb": 150,
                    "rank": 16,
                    "active": True,
                    "traffic_weight": 100,
                }
            }
        }
        reg = tmpdir / "lora_registry.json"
        reg.write_text(json.dumps(data))
        return tmpdir

    def test_run_inspect_found(self):
        from aictl.cmd.lora import run_inspect
        from aictl.runtime.lora import LoRAAdapter
        adapter = LoRAAdapter(
            name="finance-lora", base_model="llama3", path="/data",
            rank=16, active=True, vram_overhead_mb=150, traffic_weight=100,
        )
        captured = []
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            MockMgr.return_value.list_adapters.return_value = [adapter]
            with patch("aictl.cmd.lora.print_json", side_effect=captured.append):
                args = argparse.Namespace(name="finance-lora", json=True)
                ret = run_inspect(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["name"], "finance-lora")
        self.assertEqual(captured[0]["rank"], 16)

    def test_run_inspect_not_found(self):
        from aictl.cmd.lora import run_inspect
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            MockMgr.return_value.list_adapters.return_value = []
            args = argparse.Namespace(name="ghost", json=False)
            ret = run_inspect(args)
        self.assertEqual(ret, 1)

    def test_run_delete_success(self):
        from aictl.cmd.lora import run_delete
        data = {"adapters": {"del-lora": {"name": "del-lora", "base_model": "x",
                                           "path": "", "vram_overhead_mb": 100,
                                           "rank": 8, "active": True, "traffic_weight": 100}},
                "bases": {}}
        saved = []
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            inst = MockMgr.return_value
            inst._load.return_value = data
            inst._save.side_effect = saved.append
            args = argparse.Namespace(name="del-lora", json=False)
            ret = run_delete(args)
        self.assertEqual(ret, 0)
        self.assertNotIn("del-lora", saved[0]["adapters"])

    def test_run_delete_not_found(self):
        from aictl.cmd.lora import run_delete
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            MockMgr.return_value._load.return_value = {"adapters": {}, "bases": {}}
            args = argparse.Namespace(name="ghost", json=False)
            ret = run_delete(args)
        self.assertEqual(ret, 1)

    def test_run_activate(self):
        from aictl.cmd.lora import run_activate
        data = {"adapters": {"my-lora": {"name": "my-lora", "base_model": "x",
                                          "path": "", "vram_overhead_mb": 100,
                                          "rank": 16, "active": False, "traffic_weight": 100}},
                "bases": {}}
        saved = []
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            inst = MockMgr.return_value
            inst._load.return_value = data
            inst._save.side_effect = saved.append
            args = argparse.Namespace(name="my-lora", json=False)
            ret = run_activate(args)
        self.assertEqual(ret, 0)
        self.assertTrue(saved[0]["adapters"]["my-lora"]["active"])

    def test_run_deactivate(self):
        from aictl.cmd.lora import run_deactivate
        data = {"adapters": {"my-lora": {"name": "my-lora", "base_model": "x",
                                          "path": "", "vram_overhead_mb": 100,
                                          "rank": 16, "active": True, "traffic_weight": 100}},
                "bases": {}}
        saved = []
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            inst = MockMgr.return_value
            inst._load.return_value = data
            inst._save.side_effect = saved.append
            args = argparse.Namespace(name="my-lora", json=False)
            ret = run_deactivate(args)
        self.assertEqual(ret, 0)
        self.assertFalse(saved[0]["adapters"]["my-lora"]["active"])

    def test_run_activate_not_found(self):
        from aictl.cmd.lora import run_activate
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            MockMgr.return_value._load.return_value = {"adapters": {}, "bases": {}}
            args = argparse.Namespace(name="ghost", json=False)
            ret = run_activate(args)
        self.assertEqual(ret, 1)

    def test_run_deactivate_not_found(self):
        from aictl.cmd.lora import run_deactivate
        with patch("aictl.cmd.lora.LoRAManager") as MockMgr:
            MockMgr.return_value._load.return_value = {"adapters": {}, "bases": {}}
            args = argparse.Namespace(name="ghost", json=False)
            ret = run_deactivate(args)
        self.assertEqual(ret, 1)

    def test_lora_full_roundtrip(self):
        """Integration: add → deactivate → activate → delete via real LoRAManager."""
        from aictl.runtime.lora import LoRAManager, LoRAAdapter
        from aictl.cmd.lora import run_activate, run_deactivate, run_delete
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        mgr = LoRAManager(state_dir=tmpdir)
        adapter = LoRAAdapter(name="rt-lora", base_model="llama3",
                              rank=8, active=True)
        mgr.register_adapter(adapter)

        with patch("aictl.cmd.lora.LoRAManager", return_value=mgr):
            args = argparse.Namespace(name="rt-lora", json=False)
            self.assertEqual(run_deactivate(args), 0)
            loaded = mgr._load()
            self.assertFalse(loaded["adapters"]["rt-lora"]["active"])

            self.assertEqual(run_activate(args), 0)
            loaded = mgr._load()
            self.assertTrue(loaded["adapters"]["rt-lora"]["active"])

            self.assertEqual(run_delete(args), 0)
            loaded = mgr._load()
            self.assertNotIn("rt-lora", loaded["adapters"])


if __name__ == "__main__":
    unittest.main()
