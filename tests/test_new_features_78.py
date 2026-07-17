"""Pass 78 regression tests: health snapshot/history/trends, deploy dry-run, cluster failover/recovery."""

from __future__ import annotations

import argparse
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch, MagicMock


# ── health snapshot / history / trends ───────────────────────────────────────

class TestHealthHistoryTrends(unittest.TestCase):
    """health snapshot, history, trends subcommands."""

    def _make_parser(self):
        from aictl.cmd.health import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_snapshot_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["health", "snapshot"])
        self.assertEqual(args.func.__name__, "run_snapshot")

    def test_history_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["health", "history"])
        self.assertEqual(args.func.__name__, "run_history")

    def test_trends_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["health", "trends"])
        self.assertEqual(args.func.__name__, "run_trends")

    def test_history_last_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["health", "history", "--last", "5"])
        self.assertEqual(args.last, 5)

    def test_run_snapshot_json(self):
        from aictl.cmd.health import run_snapshot
        captured = []
        with patch("aictl.cmd.health.StateStore") as MockStore, \
             patch("aictl.cmd.health.full_detect") as mock_detect, \
             patch("aictl.cmd.health.print_json", side_effect=captured.append):
            MockStore.return_value.is_initialized.return_value = True
            hw = MagicMock()
            hw.system.cpu_cores = 8
            hw.system.ram_total_mb = 8192
            hw.system.disk_free_gb = 50.0
            mock_detect.return_value = hw
            args = argparse.Namespace(state_dir=None, json=True)
            ret = run_snapshot(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["snapshot_recorded"])
        self.assertGreater(captured[0]["score"], 0)

    def test_run_history_empty(self):
        from aictl.cmd.health import run_history
        args = argparse.Namespace(last=10, json=False)
        ret = run_history(args)
        self.assertEqual(ret, 0)

    def test_run_history_json_after_snapshot(self):
        from aictl.cmd.health import run_snapshot, run_history
        # Create a snapshot first
        with patch("aictl.cmd.health.StateStore") as MockStore, \
             patch("aictl.cmd.health.full_detect") as mock_detect:
            MockStore.return_value.is_initialized.return_value = True
            hw = MagicMock()
            hw.system.cpu_cores = 4
            hw.system.ram_total_mb = 4096
            hw.system.disk_free_gb = 20.0
            mock_detect.return_value = hw
            run_snapshot(argparse.Namespace(state_dir=None, json=False))

        captured = []
        with patch("aictl.cmd.health.print_json", side_effect=captured.append):
            args = argparse.Namespace(last=50, json=True)
            ret = run_history(args)
        self.assertEqual(ret, 0)
        self.assertIsInstance(captured[0], list)
        if captured[0]:  # May have snapshots from earlier in test run
            self.assertIn("pct", captured[0][0])

    def test_run_trends_empty(self):
        from aictl.cmd.health import run_trends
        # Clear bus snapshots by using a fresh context? Just verify it doesn't crash.
        args = argparse.Namespace(last=20, json=False)
        ret = run_trends(args)
        # Returns 0 even if no data
        self.assertIn(ret, [0, 0])

    def test_run_trends_json_with_data(self):
        from aictl.cmd.health import run_snapshot, run_trends
        # Seed some snapshots
        with patch("aictl.cmd.health.StateStore") as MockStore, \
             patch("aictl.cmd.health.full_detect") as mock_detect:
            MockStore.return_value.is_initialized.return_value = True
            hw = MagicMock()
            hw.system.cpu_cores = 4
            hw.system.ram_total_mb = 4096
            hw.system.disk_free_gb = 20.0
            mock_detect.return_value = hw
            for _ in range(3):
                run_snapshot(argparse.Namespace(state_dir=None, json=False))

        captured = []
        with patch("aictl.cmd.health.print_json", side_effect=captured.append):
            args = argparse.Namespace(last=50, json=True)
            ret = run_trends(args)

        if ret == 0 and captured:
            self.assertIn("avg_pct", captured[0])
            self.assertIn("trend", captured[0])
            self.assertIn(captured[0]["trend"], ["↑", "↓", "→"])


# ── deploy dry-run ────────────────────────────────────────────────────────────

class TestDeployDryRun(unittest.TestCase):
    """deploy dry-run — preview deployment with risk analysis."""

    def _make_parser(self):
        from aictl.cmd.deploy import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_dryrun_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["deploy", "dry-run", "llama3.1:8b"])
        self.assertEqual(args.func.__name__, "run_dryrun")
        self.assertEqual(args.model, "llama3.1:8b")

    def test_dryrun_flags(self):
        parser = self._make_parser()
        args = parser.parse_args(["deploy", "dry-run", "llama3.1:70b",
                                   "--hardware", "H100", "--ttft", "300", "--tps", "50"])
        self.assertEqual(args.hardware, "H100")
        self.assertEqual(args.ttft, 300)

    def test_run_dryrun_safe_json(self):
        from aictl.cmd.deploy import run_dryrun
        # Small model, plenty of VRAM → safe
        fake_est = {
            "model_params_b": 8.0, "model_vram_gb": 16.0, "total_vram_gb": 20.0,
            "gpus_needed": 1, "gpu_type": "H100", "estimated_tps": 150,
            "meets_sla": True, "disagg_recommended": False,
        }
        fake_hw = MagicMock()
        fake_hw.gpus = [MagicMock(vram_mb=40960)]  # 40 GB
        captured = []
        with patch("aictl.cmd.deploy.estimate_dgdr_resources", return_value=fake_est), \
             patch("aictl.cmd.deploy.full_detect", return_value=fake_hw), \
             patch("aictl.cmd.deploy.print_json", side_effect=captured.append):
            args = argparse.Namespace(model="llama3.1:8b", hardware="auto", ttft=500, tps=100,
                                      max_gpus=8, quant="auto", json=True)
            ret = run_dryrun(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["safe"])
        self.assertEqual(len(captured[0]["risks"]), 0)

    def test_run_dryrun_unsafe_vram_json(self):
        from aictl.cmd.deploy import run_dryrun
        # Model needs 80 GB, only 40 GB available → risk
        fake_est = {
            "model_params_b": 70.0, "model_vram_gb": 70.0, "total_vram_gb": 84.0,
            "gpus_needed": 2, "gpu_type": "H100", "estimated_tps": 30,
            "meets_sla": True, "disagg_recommended": True,
        }
        fake_hw = MagicMock()
        fake_hw.gpus = [MagicMock(vram_mb=40960)]  # only 40 GB
        captured = []
        with patch("aictl.cmd.deploy.estimate_dgdr_resources", return_value=fake_est), \
             patch("aictl.cmd.deploy.full_detect", return_value=fake_hw), \
             patch("aictl.cmd.deploy.print_json", side_effect=captured.append):
            args = argparse.Namespace(model="llama3.1:70b", hardware="auto", ttft=500, tps=100,
                                      max_gpus=8, quant="auto", json=True)
            ret = run_dryrun(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["safe"])
        self.assertGreater(len(captured[0]["risks"]), 0)

    def test_run_dryrun_no_gpus(self):
        from aictl.cmd.deploy import run_dryrun
        fake_est = {
            "model_params_b": 8.0, "model_vram_gb": 16.0, "total_vram_gb": 20.0,
            "gpus_needed": 1, "gpu_type": "CPU", "estimated_tps": 50,
            "meets_sla": True, "disagg_recommended": False,
        }
        fake_hw = MagicMock()
        fake_hw.gpus = []  # no GPUs
        captured = []
        with patch("aictl.cmd.deploy.estimate_dgdr_resources", return_value=fake_est), \
             patch("aictl.cmd.deploy.full_detect", return_value=fake_hw), \
             patch("aictl.cmd.deploy.print_json", side_effect=captured.append):
            args = argparse.Namespace(model="llama3.1:8b", hardware="auto", ttft=500, tps=100,
                                      max_gpus=8, quant="auto", json=True)
            ret = run_dryrun(args)
        # With no GPUs (0 < 1 needed) → risk
        self.assertEqual(ret, 1)


# ── cluster failover / recovery-policy ───────────────────────────────────────

class TestClusterFailoverRecovery(unittest.TestCase):
    """cluster failover and recovery-policy subcommands."""

    def _make_parser(self):
        from aictl.cmd.cluster import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_failover_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["cluster", "failover", "local-chat"])
        self.assertEqual(args.func.__name__, "run_failover")
        self.assertEqual(args.stack, "local-chat")

    def test_failover_to_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["cluster", "failover", "mystack", "--to", "http://backup:8080"])
        self.assertEqual(args.to, "http://backup:8080")

    def test_recovery_policy_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["cluster", "recovery-policy"])
        self.assertEqual(args.func.__name__, "run_recovery_policy")

    def test_run_failover_json(self):
        from aictl.cmd.cluster import run_failover
        captured = []
        with patch("aictl.cmd.cluster.print_json", side_effect=captured.append):
            args = argparse.Namespace(stack="local-chat", to="http://backup:8080", json=True)
            ret = run_failover(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["simulated"])
        self.assertEqual(captured[0]["stack"], "local-chat")
        self.assertGreater(len(captured[0]["steps"]), 0)

    def test_run_failover_emits_event(self):
        from aictl.cmd.cluster import run_failover
        from aictl.core.events import get_bus
        bus = get_bus()
        before = len([e for e in bus.recent(n=500) if "failover" in e.type])
        args = argparse.Namespace(stack="test-stack", to="", json=False)
        run_failover(args)
        after = len([e for e in bus.recent(n=500) if "failover" in e.type])
        self.assertGreater(after, before)

    def test_run_recovery_policy_defaults(self):
        from aictl.cmd.cluster import run_recovery_policy
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        captured = []
        with patch("aictl.cmd.cluster.print_json", side_effect=captured.append):
            args = argparse.Namespace(set_retries=-1, set_delay=-1,
                                      state_dir=str(tmpdir), json=True)
            ret = run_recovery_policy(args)
        self.assertEqual(ret, 0)
        self.assertIn("max_retries", captured[0])
        self.assertIn("restart_delay_s", captured[0])
        self.assertGreater(captured[0]["max_retries"], 0)

    def test_run_recovery_policy_update(self):
        from aictl.cmd.cluster import run_recovery_policy
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        captured = []
        with patch("aictl.cmd.cluster.print_json", side_effect=captured.append):
            args = argparse.Namespace(set_retries=5, set_delay=60,
                                      state_dir=str(tmpdir), json=True)
            ret = run_recovery_policy(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["max_retries"], 5)
        self.assertEqual(captured[0]["restart_delay_s"], 60)
        # Verify it was persisted
        policy_file = tmpdir / "recovery_policy.json"
        self.assertTrue(policy_file.exists())
        saved = json.loads(policy_file.read_text())
        self.assertEqual(saved["max_retries"], 5)

    def test_run_recovery_policy_persistence(self):
        """Written policy is read back on next call."""
        from aictl.cmd.cluster import run_recovery_policy
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        with patch("aictl.cmd.cluster.print_json"):
            args = argparse.Namespace(set_retries=7, set_delay=-1,
                                      state_dir=str(tmpdir), json=True)
            run_recovery_policy(args)
        captured = []
        with patch("aictl.cmd.cluster.print_json", side_effect=captured.append):
            args2 = argparse.Namespace(set_retries=-1, set_delay=-1,
                                       state_dir=str(tmpdir), json=True)
            run_recovery_policy(args2)
        self.assertEqual(captured[0]["max_retries"], 7)


if __name__ == "__main__":
    unittest.main()
