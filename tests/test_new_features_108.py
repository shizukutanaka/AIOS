"""Pass 108 (loop): --json contract on more 'action' commands.

A real-CLI --json sweep of work/action commands found three more stdout-pollution
violations of the documented universal --json flag:
  - warmup run: empty case printed "No model usage history" before the --json
    check -> now emits [].
  - fit --gpu auto on a no-GPU machine: _analyze_cpu printed human warnings even
    under --json -> now emits a JSON object.
  - troubleshoot: the --simulate path delegated to fit with json hardcoded False,
    and the symptom diagnosers printed human fix-walls under --json -> simulate
    now passes --json through; symptom paths emit a JSON diagnosis summary.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock


def _json_out(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(args)
    return json.loads(buf.getvalue())  # raises if not pure JSON


class TestWarmupRunJson(unittest.TestCase):
    def test_empty_history_emits_json_list(self):
        from aictl.cmd.warmup import run_warmup
        with patch("aictl.cmd.warmup.WarmupManager") as M:
            M.return_value.get_warmup_candidates.return_value = []
            d = _json_out(run_warmup, argparse.Namespace(state_dir=None, top=3, json=True))
        self.assertEqual(d, [])


class TestFitCpuJson(unittest.TestCase):
    def test_no_gpu_path_emits_json(self):
        from aictl.cmd.fit import run
        hw = MagicMock()
        hw.gpus = []
        hw.system.ram_total_mb = 16000
        with patch("aictl.runtime.broker.full_detect", return_value=hw):
            d = _json_out(run, argparse.Namespace(model="llama3.1:8b", gpu="auto",
                          context=8192, concurrent=1, use_case="", json=True))
        self.assertEqual(d["gpu"], "CPU")
        self.assertIn("fits", d)


class TestTroubleshootJson(unittest.TestCase):
    def test_symptom_emits_json_summary(self):
        from aictl.cmd.troubleshoot import run
        d = _json_out(run, argparse.Namespace(symptom="oom", simulate=None, json=True))
        self.assertEqual(d["symptom"], "oom")
        self.assertEqual(d["diagnosis"], "out-of-memory")

    def test_simulate_passes_json_to_fit(self):
        from aictl.cmd.troubleshoot import run
        hw = MagicMock()
        hw.gpus = []
        hw.system.ram_total_mb = 16000
        with patch("aictl.runtime.broker.full_detect", return_value=hw):
            d = _json_out(run, argparse.Namespace(symptom="auto", simulate="llama3.1:8b",
                          json=True))
        self.assertEqual(d["gpu"], "CPU")  # fit's CPU JSON, not human text


if __name__ == "__main__":
    unittest.main()
