"""Pass 107 (loop): bench input validation + clean --json output.

Two issues on the bench command:
  - run_benchmark accepted num_requests < 1 (and max_tokens < 1), producing
    nonsense output like {"requests": -5} with all-zero stats. Now rejected with
    a clear ValueError (surfaced as "Invalid input").
  - `bench --mock --json` and `bench slo --json` printed human ok() lines
    ("Mock engine started", "Benchmarking...", "SLO verification:") to stdout
    BEFORE the JSON, breaking `bench --json | jq`. Those lines are now suppressed
    under --json.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout


class TestBenchRequestValidation(unittest.TestCase):

    def test_negative_requests_rejected(self):
        from aictl.runtime.benchmark import run_benchmark
        with self.assertRaises(ValueError):
            run_benchmark(num_requests=-5)

    def test_zero_requests_rejected(self):
        from aictl.runtime.benchmark import run_benchmark
        with self.assertRaises(ValueError):
            run_benchmark(num_requests=0)

    def test_zero_max_tokens_rejected(self):
        from aictl.runtime.benchmark import run_benchmark
        with self.assertRaises(ValueError):
            run_benchmark(num_requests=1, max_tokens=0)


class TestBenchJsonClean(unittest.TestCase):

    def _run_json(self, args):
        from aictl.cmd.bench import run
        buf = io.StringIO()
        with redirect_stdout(buf):
            run(args)
        return buf.getvalue()

    def test_mock_json_stdout_is_pure_json(self):
        out = self._run_json(argparse.Namespace(
            endpoint="http://x", model="", requests=2, max_tokens=10,
            mock=True, json=True))
        d = json.loads(out)  # must parse — no "Mock engine started" noise
        self.assertEqual(d["requests"], 2)

    def test_mock_human_still_prints_messages(self):
        from aictl.cmd.bench import run
        buf = io.StringIO()
        with redirect_stdout(buf):
            run(argparse.Namespace(endpoint="http://x", model="", requests=1,
                                   max_tokens=10, mock=True, json=False))
        self.assertIn("Mock engine started", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
