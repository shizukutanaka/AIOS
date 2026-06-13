"""Pass 80 regression tests: cost forecast/providers, model cleanup, snapshot validate/purge."""

from __future__ import annotations

import argparse
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock


# ── cost forecast / providers ─────────────────────────────────────────────────

class TestCostForecastProviders(unittest.TestCase):

    def _make_parser(self):
        from aictl.cmd.cost import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_forecast_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["cost", "forecast"])
        self.assertEqual(args.func.__name__, "run_forecast")

    def test_forecast_horizon_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["cost", "forecast", "--horizon", "60"])
        self.assertEqual(args.horizon, 60)

    def test_providers_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["cost", "providers"])
        self.assertEqual(args.func.__name__, "run_providers")

    def test_run_forecast_json(self):
        from aictl.cmd.cost import run_forecast
        captured = []
        with patch("aictl.cmd.cost.print_json", side_effect=captured.append):
            args = argparse.Namespace(gpu="H100 SXM", gpus=1, horizon=90, hours=24, json=True)
            ret = run_forecast(args)
        self.assertEqual(ret, 0)
        self.assertIn("milestones", captured[0])
        self.assertGreater(len(captured[0]["milestones"]), 0)
        self.assertEqual(captured[0]["horizon_days"], 90)
        # 90-day milestone should be present
        days_list = [m["days"] for m in captured[0]["milestones"]]
        self.assertIn(90, days_list)

    def test_run_forecast_milestones_increase(self):
        from aictl.cmd.cost import run_forecast
        captured = []
        with patch("aictl.cmd.cost.print_json", side_effect=captured.append):
            args = argparse.Namespace(gpu="RTX 4090", gpus=1, horizon=90, hours=8, json=True)
            ret = run_forecast(args)
        self.assertEqual(ret, 0)
        milestones = captured[0]["milestones"]
        # Cloud cost should increase with days
        costs = [m["cloud_usd"] for m in milestones]
        self.assertEqual(costs, sorted(costs))

    def test_run_forecast_auto_detect_gpu(self):
        from aictl.cmd.cost import run_forecast
        fake_hw = MagicMock()
        fake_hw.gpus = [MagicMock(name="H100 SXM5")]
        captured = []
        with patch("aictl.cmd.cost.full_detect", return_value=fake_hw), \
             patch("aictl.cmd.cost.print_json", side_effect=captured.append):
            args = argparse.Namespace(gpu="", gpus=1, horizon=30, hours=24, json=True)
            ret = run_forecast(args)
        self.assertEqual(ret, 0)
        self.assertIn("gpu", captured[0])

    def test_run_providers_json(self):
        from aictl.cmd.cost import run_providers
        captured = []
        with patch("aictl.cmd.cost.print_json", side_effect=captured.append):
            args = argparse.Namespace(hours=24, json=True)
            ret = run_providers(args)
        self.assertEqual(ret, 0)
        self.assertIsInstance(captured[0], list)
        self.assertGreater(len(captured[0]), 0)
        first = captured[0][0]
        self.assertIn("provider", first)
        self.assertIn("gpu", first)
        self.assertIn("cloud_monthly_usd", first)

    def test_run_providers_text(self):
        from aictl.cmd.cost import run_providers
        # Should not raise and should return 0
        args = argparse.Namespace(hours=24, json=False)
        ret = run_providers(args)
        self.assertEqual(ret, 0)


# ── model cleanup ─────────────────────────────────────────────────────────────

class TestModelCleanup(unittest.TestCase):

    def _make_parser(self):
        from aictl.cmd.model import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_cleanup_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["model", "cleanup"])
        self.assertEqual(args.func.__name__, "run_cleanup")

    def test_cleanup_days_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["model", "cleanup", "--days", "60"])
        self.assertEqual(args.days, 60)

    def test_cleanup_dry_run_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["model", "cleanup", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_run_cleanup_no_models(self):
        from aictl.cmd.model import run_cleanup
        with patch("aictl.cmd.model.StateStore") as MockStore:
            MockStore.return_value.list_models.return_value = []
            args = argparse.Namespace(state_dir=None, days=30, status="", dry_run=False, json=False)
            ret = run_cleanup(args)
        self.assertEqual(ret, 0)

    def test_run_cleanup_dry_run_json(self):
        from aictl.cmd.model import run_cleanup
        old_ts = time.time() - 40 * 86400  # 40 days old
        models = [
            {"id": "abc123", "name": "llama3:8b", "status": "available", "registered_at": old_ts},
        ]
        captured = []
        with patch("aictl.cmd.model.StateStore") as MockStore, \
             patch("aictl.cmd.model.print_json", side_effect=captured.append):
            MockStore.return_value.list_models.return_value = models
            args = argparse.Namespace(state_dir=None, days=30, status="", dry_run=True, json=True)
            ret = run_cleanup(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["dry_run"])
        self.assertEqual(len(captured[0]["candidates"]), 1)
        self.assertEqual(captured[0]["removed"], 0)  # dry_run

    def test_run_cleanup_removes_stale_json(self):
        from aictl.cmd.model import run_cleanup
        old_ts = time.time() - 40 * 86400  # 40 days old
        models = [
            {"id": "abc123", "name": "stale-model", "status": "available",
             "registered_at": old_ts},
            {"id": "xyz789", "name": "fresh-model", "status": "available",
             "registered_at": time.time()},  # fresh — should not be removed
        ]
        captured = []
        with patch("aictl.cmd.model.StateStore") as MockStore, \
             patch("aictl.cmd.model._delete_models") as mock_del, \
             patch("aictl.cmd.model.print_json", side_effect=captured.append):
            MockStore.return_value.list_models.return_value = models
            args = argparse.Namespace(state_dir=None, days=30, status="", dry_run=False, json=True)
            ret = run_cleanup(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["removed"], 1)
        # Only the stale model should be in candidates
        self.assertEqual(captured[0]["candidates"][0]["id"], "abc123")
        mock_del.assert_called_once()

    def test_run_cleanup_status_filter(self):
        from aictl.cmd.model import run_cleanup
        old_ts = time.time() - 40 * 86400
        models = [
            {"id": "a1", "name": "m1", "status": "unavailable", "registered_at": old_ts},
            {"id": "a2", "name": "m2", "status": "available", "registered_at": old_ts},
        ]
        captured = []
        with patch("aictl.cmd.model.StateStore") as MockStore, \
             patch("aictl.cmd.model._delete_models"), \
             patch("aictl.cmd.model.print_json", side_effect=captured.append):
            MockStore.return_value.list_models.return_value = models
            args = argparse.Namespace(state_dir=None, days=30, status="unavailable",
                                      dry_run=False, json=True)
            ret = run_cleanup(args)
        self.assertEqual(ret, 0)
        # Only the unavailable model should be targeted
        self.assertEqual(len(captured[0]["candidates"]), 1)
        self.assertEqual(captured[0]["candidates"][0]["id"], "a1")


# ── snapshot validate / purge ─────────────────────────────────────────────────

class TestSnapshotValidatePurge(unittest.TestCase):

    def _make_parser(self):
        from aictl.cmd.snapshot import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def _make_snap_dir(self, snap_data: dict) -> tuple[pathlib.Path, str]:
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        snap_dir = tmpdir / "snapshots"
        snap_dir.mkdir()
        snap_id = snap_data.get("snapshot_id", "test_snap_001")
        snap_file = snap_dir / f"{snap_id}.json"
        snap_file.write_text(json.dumps(snap_data))
        return tmpdir, snap_id

    def test_validate_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["snapshot", "validate", "abc123"])
        self.assertEqual(args.func.__name__, "run_validate")
        self.assertEqual(args.id, "abc123")

    def test_purge_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["snapshot", "purge"])
        self.assertEqual(args.func.__name__, "run_purge")

    def test_purge_max_age_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["snapshot", "purge", "--max-age", "14"])
        self.assertEqual(args.max_age, 14)

    def test_purge_keep_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["snapshot", "purge", "--keep", "3"])
        self.assertEqual(args.keep, 3)

    def test_run_validate_not_found(self):
        from aictl.cmd.snapshot import run_validate
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        (tmpdir / "snapshots").mkdir()
        args = argparse.Namespace(id="ghost", state_dir=tmpdir, json=False)
        ret = run_validate(args)
        self.assertEqual(ret, 1)

    def test_run_validate_valid_json(self):
        from aictl import __version__
        from aictl.cmd.snapshot import run_validate
        snap_data = {
            "snapshot_id": "valid_snap_001",
            "created_at": time.time(),
            "version": __version__,
            "stacks": [{"name": "local-chat"}],
            "models": [{"name": "llama3:8b"}],
        }
        tmpdir, snap_id = self._make_snap_dir(snap_data)
        captured = []
        with patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
            args = argparse.Namespace(id=snap_id, state_dir=tmpdir, json=True)
            ret = run_validate(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["valid"])
        self.assertEqual(len(captured[0]["problems"]), 0)

    def test_run_validate_missing_fields(self):
        from aictl.cmd.snapshot import run_validate
        snap_data = {
            "snapshot_id": "bad_snap_001",
            # missing: created_at, version, stacks, models
        }
        tmpdir, snap_id = self._make_snap_dir(snap_data)
        captured = []
        with patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
            args = argparse.Namespace(id=snap_id, state_dir=tmpdir, json=True)
            ret = run_validate(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["valid"])
        self.assertGreater(len(captured[0]["problems"]), 0)

    def test_run_validate_version_mismatch(self):
        from aictl.cmd.snapshot import run_validate
        snap_data = {
            "snapshot_id": "old_snap_001",
            "created_at": time.time(),
            "version": "0.0.0",  # definitely wrong
            "stacks": [], "models": [],
        }
        tmpdir, snap_id = self._make_snap_dir(snap_data)
        captured = []
        with patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
            args = argparse.Namespace(id=snap_id, state_dir=tmpdir, json=True)
            ret = run_validate(args)
        # Returns 1 due to version mismatch
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["valid"])
        version_probs = [p for p in captured[0]["problems"] if "Version" in p]
        self.assertGreater(len(version_probs), 0)

    def test_run_purge_no_candidates(self):
        from aictl.cmd.snapshot import run_purge
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        (tmpdir / "snapshots").mkdir()
        args = argparse.Namespace(state_dir=tmpdir, max_age=7, keep=1,
                                  dry_run=False, json=False)
        ret = run_purge(args)
        self.assertEqual(ret, 0)

    def test_run_purge_removes_old_json(self):
        from aictl.cmd.snapshot import run_purge
        now = time.time()
        old_ts = now - 10 * 86400  # 10 days old
        snaps = [
            {"id": "snap_old", "created_at": old_ts, "version": "1.0", "stacks": 1, "models": 1, "size_bytes": 0},
            {"id": "snap_new", "created_at": now, "version": "1.0", "stacks": 1, "models": 1, "size_bytes": 0},
        ]
        captured = []
        with patch("aictl.cmd.snapshot.SnapshotManager") as MockMgr, \
             patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
            MockMgr.return_value.list_snapshots.return_value = snaps
            MockMgr.return_value.delete.return_value = True
            args = argparse.Namespace(state_dir=None, max_age=7, keep=1,
                                      dry_run=False, json=True)
            ret = run_purge(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["purged"], 1)
        self.assertIn("snap_old", captured[0]["ids"])
        self.assertNotIn("snap_new", captured[0]["ids"])

    def test_run_purge_dry_run(self):
        from aictl.cmd.snapshot import run_purge
        old_ts = time.time() - 10 * 86400
        snaps = [
            {"id": "snap_old", "created_at": old_ts, "version": "1.0", "stacks": 1, "models": 1, "size_bytes": 0},
        ]
        captured = []
        with patch("aictl.cmd.snapshot.SnapshotManager") as MockMgr, \
             patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
            MockMgr.return_value.list_snapshots.return_value = snaps
            args = argparse.Namespace(state_dir=None, max_age=7, keep=0,
                                      dry_run=True, json=True)
            ret = run_purge(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["dry_run"])
        self.assertEqual(captured[0]["purged"], 0)  # dry_run → no actual deletion
        # delete should NOT have been called
        MockMgr.return_value.delete.assert_not_called()

    def test_run_purge_keep_protects_newest(self):
        from aictl.cmd.snapshot import run_purge
        now = time.time()
        snaps = [
            {"id": f"snap_{i:02d}", "created_at": now - (i+1)*86400 * 2,
             "version": "1.0", "stacks": 1, "models": 1, "size_bytes": 0}
            for i in range(5)
        ]
        captured = []
        with patch("aictl.cmd.snapshot.SnapshotManager") as MockMgr, \
             patch("aictl.cmd.snapshot.print_json", side_effect=captured.append):
            MockMgr.return_value.list_snapshots.return_value = snaps
            MockMgr.return_value.delete.return_value = True
            args = argparse.Namespace(state_dir=None, max_age=1, keep=2,
                                      dry_run=False, json=True)
            ret = run_purge(args)
        self.assertEqual(ret, 0)
        # 2 newest should be protected, up to 3 others may be deleted
        purged_ids = captured[0].get("ids", [])
        # snap_00 is newest (smallest i → largest created_at), snap_01 second — both protected
        self.assertNotIn("snap_00", purged_ids)
        self.assertNotIn("snap_01", purged_ids)


if __name__ == "__main__":
    unittest.main()
