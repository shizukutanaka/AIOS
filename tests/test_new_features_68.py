"""Pass 68 regression tests: aictl top, gpu_live_stats."""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch, MagicMock


class TestGpuLiveStats(unittest.TestCase):
    """broker.gpu_live_stats parses nvidia-smi live output."""

    def test_returns_empty_when_no_nvidia_smi(self):
        from aictl.runtime.broker import gpu_live_stats
        with patch("shutil.which", return_value=None):
            stats = gpu_live_stats()
        self.assertEqual(stats, [])

    def test_returns_empty_when_query_fails(self):
        from aictl.runtime.broker import gpu_live_stats
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
             patch("aictl.runtime.broker._run", return_value=None):
            stats = gpu_live_stats()
        self.assertEqual(stats, [])

    def test_parses_single_gpu(self):
        from aictl.runtime.broker import gpu_live_stats
        csv = "0, NVIDIA H100 80GB HBM3, 45, 30000, 81920, 62, 350.5"
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
             patch("aictl.runtime.broker._run", return_value=csv):
            stats = gpu_live_stats()
        self.assertEqual(len(stats), 1)
        g = stats[0]
        self.assertEqual(g["index"], 0)
        self.assertEqual(g["name"], "NVIDIA H100 80GB HBM3")
        self.assertEqual(g["util_pct"], 45)
        self.assertEqual(g["mem_used_mb"], 30000)
        self.assertEqual(g["mem_total_mb"], 81920)
        self.assertEqual(g["temp_c"], 62)
        self.assertEqual(g["power_w"], 350.5)

    def test_parses_multiple_gpus(self):
        from aictl.runtime.broker import gpu_live_stats
        csv = ("0, NVIDIA A100, 10, 5000, 40960, 50, 100\n"
               "1, NVIDIA A100, 90, 39000, 40960, 75, 300")
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
             patch("aictl.runtime.broker._run", return_value=csv):
            stats = gpu_live_stats()
        self.assertEqual(len(stats), 2)
        self.assertEqual(stats[1]["util_pct"], 90)

    def test_handles_malformed_values_gracefully(self):
        from aictl.runtime.broker import gpu_live_stats
        # [N/A] values from nvidia-smi when metric unavailable
        csv = "0, NVIDIA T4, [N/A], 1000, 16000, [N/A], [N/A]"
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
             patch("aictl.runtime.broker._run", return_value=csv):
            stats = gpu_live_stats()
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["util_pct"], 0)  # N/A coerced to 0
        self.assertEqual(stats[0]["mem_used_mb"], 1000)


class TestTopCommand(unittest.TestCase):
    """aictl top renders GPU stats and loaded models."""

    def _make_parser(self):
        from aictl.cmd.top import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_top_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["top"])
        self.assertEqual(args.func.__name__, "run")

    def test_watch_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["top", "--watch"])
        self.assertTrue(args.watch)

    def test_interval_default(self):
        parser = self._make_parser()
        args = parser.parse_args(["top", "--watch"])
        self.assertEqual(args.interval, 2)

    def test_json_snapshot(self):
        from aictl.cmd.top import run
        fake_snapshot = {
            "gpus": [{"index": 0, "name": "H100", "util_pct": 50,
                      "mem_used_mb": 1000, "mem_total_mb": 80000,
                      "temp_c": 60, "power_w": 300.0}],
            "models": [{"engine": "vllm", "model": "llama3", "vram_mb": 0}],
        }
        captured = []
        with patch("aictl.cmd.top._collect", return_value=fake_snapshot), \
             patch("aictl.cmd.top.print_json", side_effect=captured.append):
            args = argparse.Namespace(json=True, watch=False, interval=2)
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertIn("gpus", captured[0])
        self.assertIn("models", captured[0])

    def test_run_renders_without_watch(self):
        from aictl.cmd.top import run
        fake_snapshot = {"gpus": [], "models": []}
        with patch("aictl.cmd.top._collect", return_value=fake_snapshot), \
             patch("aictl.cmd.top._render") as mock_render:
            args = argparse.Namespace(json=False, watch=False, interval=2)
            ret = run(args)
        self.assertEqual(ret, 0)
        mock_render.assert_called_once()

    def test_collect_combines_gpus_and_models(self):
        from aictl.cmd.top import _collect
        with patch("aictl.runtime.broker.gpu_live_stats", return_value=[{"index": 0}]), \
             patch("aictl.cmd.top._loaded_models", return_value=[{"engine": "vllm"}]):
            snap = _collect()
        self.assertEqual(len(snap["gpus"]), 1)
        self.assertEqual(len(snap["models"]), 1)

    def test_render_handles_empty_gpus(self):
        from aictl.cmd.top import _render
        # Should not raise with no GPUs and no models
        _render({"gpus": [], "models": []})

    def test_top_registered_in_main(self):
        import importlib
        main = importlib.import_module("aictl.__main__")
        parser = main.build_parser()
        args = parser.parse_args(["top"])
        self.assertEqual(args.func.__name__, "run")


if __name__ == "__main__":
    unittest.main()
