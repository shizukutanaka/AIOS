"""Pass 65 regression tests: status --watch, node cordon/uncordon/drain."""

from __future__ import annotations

import argparse
import pathlib
import tempfile
import unittest
from unittest.mock import patch, MagicMock, call


class TestStatusWatch(unittest.TestCase):
    """status --watch and --interval flags."""

    def _make_parser(self):
        from aictl.cmd.status import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_watch_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["status", "--watch"])
        self.assertTrue(args.watch)

    def test_watch_default_is_false(self):
        parser = self._make_parser()
        args = parser.parse_args(["status"])
        self.assertFalse(args.watch)

    def test_interval_default(self):
        parser = self._make_parser()
        args = parser.parse_args(["status", "--watch"])
        self.assertEqual(args.interval, 5)

    def test_custom_interval(self):
        parser = self._make_parser()
        args = parser.parse_args(["status", "--watch", "--interval", "10"])
        self.assertEqual(args.interval, 10)

    def test_watch_calls_run_once_then_keyboard_interrupt(self):
        """_run_watch calls _run_once at least once, exits 0 on Ctrl-C."""
        from aictl.cmd.status import _run_watch

        call_count = [0]

        def fake_run_once(args):
            call_count[0] += 1
            if call_count[0] >= 1:
                raise KeyboardInterrupt

        with patch("aictl.cmd.status._run_once", side_effect=fake_run_once), \
             patch("os.system"):
            args = argparse.Namespace(
                interval=1, watch=True, brief=False, json=False, state_dir=None
            )
            ret = _run_watch(args)
        self.assertEqual(ret, 0)
        self.assertGreaterEqual(call_count[0], 1)

    def test_run_delegates_to_watch_when_flag_set(self):
        from aictl.cmd.status import run

        watch_called = []
        def fake_watch(args):
            watch_called.append(True)
            return 0

        with patch("aictl.cmd.status._run_watch", side_effect=fake_watch):
            args = argparse.Namespace(
                watch=True, interval=5, brief=False, json=False, state_dir=None
            )
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertTrue(watch_called)

    def test_run_delegates_to_once_when_no_watch(self):
        from aictl.cmd.status import run

        once_called = []
        def fake_once(args):
            once_called.append(True)
            return 0

        with patch("aictl.cmd.status._run_once", side_effect=fake_once):
            args = argparse.Namespace(
                watch=False, interval=5, brief=False, json=False, state_dir=None
            )
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertTrue(once_called)


class TestNodeCordonDrainUncordon(unittest.TestCase):
    """node cordon, uncordon, drain subcommands."""

    def _make_parser(self):
        from aictl.cmd.node import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def _fake_mgr(self, peer_status="active"):
        from aictl.runtime.nodes import PeerNode, ClusterState
        peer = PeerNode(
            node_id="abc123def456",
            hostname="worker-1",
            address="192.168.1.10",
            status=peer_status,
        )
        cs = ClusterState(mode="cluster", peers=[peer])
        mgr = MagicMock()
        mgr.load_cluster.return_value = cs
        mgr.save_cluster = MagicMock()
        return mgr, peer

    def test_cordon_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["node", "cordon", "worker-1"])
        self.assertEqual(args.func.__name__, "run_cordon")
        self.assertEqual(args.node_id, "worker-1")

    def test_uncordon_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["node", "uncordon", "worker-1"])
        self.assertEqual(args.func.__name__, "run_uncordon")

    def test_drain_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["node", "drain", "worker-1"])
        self.assertEqual(args.func.__name__, "run_drain")

    def test_drain_force_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["node", "drain", "worker-1", "--force"])
        self.assertTrue(args.force)

    def test_cordon_returns_0_when_node_found(self):
        from aictl.cmd.node import run_cordon
        mgr, peer = self._fake_mgr()

        with patch("aictl.cmd.node.StateStore"), \
             patch("aictl.cmd.node.NodeManager", return_value=mgr):
            args = argparse.Namespace(node_id="worker-1", json=True, state_dir=None)
            ret = run_cordon(args)
        self.assertEqual(ret, 0)
        mgr.save_cluster.assert_called_once()

    def test_cordon_returns_1_when_not_found(self):
        from aictl.cmd.node import run_cordon
        from aictl.runtime.nodes import ClusterState
        mgr = MagicMock()
        mgr.load_cluster.return_value = ClusterState(mode="cluster", peers=[])

        captured = []
        with patch("aictl.cmd.node.StateStore"), \
             patch("aictl.cmd.node.NodeManager", return_value=mgr), \
             patch("aictl.cmd.node.print_json", side_effect=captured.append):
            args = argparse.Namespace(node_id="missing", json=True, state_dir=None)
            ret = run_cordon(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["success"])

    def test_cordon_sets_status_cordoned(self):
        from aictl.cmd.node import run_cordon
        mgr, peer = self._fake_mgr()
        saved_states = []

        def capture_save(cs):
            saved_states.append([p.status for p in cs.peers])

        mgr.save_cluster.side_effect = capture_save

        captured = []
        with patch("aictl.cmd.node.StateStore"), \
             patch("aictl.cmd.node.NodeManager", return_value=mgr), \
             patch("aictl.cmd.node.print_json", side_effect=captured.append):
            args = argparse.Namespace(node_id="worker-1", json=True, state_dir=None)
            run_cordon(args)
        self.assertEqual(saved_states[0], ["cordoned"])

    def test_uncordon_sets_status_active(self):
        from aictl.cmd.node import run_uncordon
        mgr, peer = self._fake_mgr("cordoned")
        saved_states = []

        def capture_save(cs):
            saved_states.append([p.status for p in cs.peers])

        mgr.save_cluster.side_effect = capture_save

        captured = []
        with patch("aictl.cmd.node.StateStore"), \
             patch("aictl.cmd.node.NodeManager", return_value=mgr), \
             patch("aictl.cmd.node.print_json", side_effect=captured.append):
            args = argparse.Namespace(node_id="worker-1", json=True, state_dir=None)
            ret = run_uncordon(args)
        self.assertEqual(ret, 0)
        self.assertEqual(saved_states[0], ["active"])

    def test_drain_sets_status_cordoned_after_eviction(self):
        from aictl.cmd.node import run_drain
        from aictl.core.state import NodeState
        mgr, peer = self._fake_mgr()
        saved_statuses = []

        def capture_save(cs):
            saved_statuses.extend([p.status for p in cs.peers])

        mgr.save_cluster.side_effect = capture_save

        mock_node_state = NodeState(node_id="different-node")  # not same as peer

        captured = []
        with patch("aictl.cmd.node.StateStore") as mock_store_cls, \
             patch("aictl.cmd.node.NodeManager", return_value=mgr), \
             patch("aictl.cmd.node.print_json", side_effect=captured.append):
            mock_store = MagicMock()
            mock_store.load_node.return_value = mock_node_state
            mock_store_cls.return_value = mock_store
            args = argparse.Namespace(node_id="worker-1", json=True, force=False, state_dir=None)
            ret = run_drain(args)
        self.assertEqual(ret, 0)
        # After drain, node should end as cordoned
        self.assertIn("cordoned", saved_statuses)

    def test_drain_json_output_has_required_keys(self):
        from aictl.cmd.node import run_drain
        from aictl.core.state import NodeState
        mgr, peer = self._fake_mgr()

        captured = []
        with patch("aictl.cmd.node.StateStore") as mock_store_cls, \
             patch("aictl.cmd.node.NodeManager", return_value=mgr), \
             patch("aictl.cmd.node.print_json", side_effect=captured.append):
            mock_store = MagicMock()
            mock_store.load_node.return_value = NodeState(node_id="other")
            mock_store_cls.return_value = mock_store
            args = argparse.Namespace(node_id="abc123", json=True, force=False, state_dir=None)
            run_drain(args)
        data = captured[0]
        for key in ("success", "node_id", "status", "evicted"):
            self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
