"""Tests for aictl eval and aictl spec — the v1.7.0 deep research features."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestEvalCreate(unittest.TestCase):
    def test_create_writes_template(self):
        from aictl.cmd.eval import run_create
        with tempfile.TemporaryDirectory() as td:
            class FA:
                suite = str(Path(td) / "suite.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_create(FA())
            self.assertEqual(rc, 0)
            suite = json.loads(Path(FA.suite).read_text())
            self.assertIn("cases", suite)
            self.assertGreater(len(suite["cases"]), 0)

    def test_create_fails_if_exists(self):
        from aictl.cmd.eval import run_create
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "suite.json"
            path.write_text("{}")
            class FA:
                suite = str(path)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_create(FA())
            self.assertNotEqual(rc, 0)


class TestEvalAssertions(unittest.TestCase):
    """Each assertion type must work correctly."""

    def _check(self, assertion, output, latency_ms=0, cost_usd=0.0):
        from aictl.cmd.eval import _check_assertion
        return _check_assertion(assertion, output, latency_ms, cost_usd)

    def test_contains_pass(self):
        passed, _ = self._check({"type": "contains", "value": "hello"}, "hello world")
        self.assertTrue(passed)

    def test_contains_fail(self):
        passed, _ = self._check({"type": "contains", "value": "xyz"}, "hello world")
        self.assertFalse(passed)

    def test_not_contains_pass(self):
        passed, _ = self._check({"type": "not_contains", "value": "bad"}, "good output")
        self.assertTrue(passed)

    def test_max_length_pass(self):
        passed, _ = self._check({"type": "max_length", "value": 100}, "short")
        self.assertTrue(passed)

    def test_max_length_fail(self):
        passed, _ = self._check({"type": "max_length", "value": 3}, "this is too long")
        self.assertFalse(passed)

    def test_min_length_pass(self):
        passed, _ = self._check({"type": "min_length", "value": 3}, "hello")
        self.assertTrue(passed)

    def test_min_length_fail(self):
        passed, _ = self._check({"type": "min_length", "value": 100}, "short")
        self.assertFalse(passed)

    def test_json_valid_pass(self):
        passed, _ = self._check({"type": "json_valid"}, '{"key": "value"}')
        self.assertTrue(passed)

    def test_json_valid_fail(self):
        passed, _ = self._check({"type": "json_valid"}, "not json")
        self.assertFalse(passed)

    def test_regex_pass(self):
        passed, _ = self._check({"type": "regex", "value": r"\d+"}, "answer is 42")
        self.assertTrue(passed)

    def test_regex_fail(self):
        passed, _ = self._check({"type": "regex", "value": r"^\d+$"}, "not a number")
        self.assertFalse(passed)

    def test_starts_with_pass(self):
        passed, _ = self._check({"type": "starts_with", "value": "Hello"}, "Hello world")
        self.assertTrue(passed)

    def test_latency_pass(self):
        passed, _ = self._check({"type": "latency_ms", "value": 1000}, "output", latency_ms=500)
        self.assertTrue(passed)

    def test_latency_fail(self):
        passed, _ = self._check({"type": "latency_ms", "value": 100}, "output", latency_ms=500)
        self.assertFalse(passed)

    def test_cost_pass(self):
        passed, _ = self._check({"type": "cost_usd", "value": 0.01}, "output", cost_usd=0.001)
        self.assertTrue(passed)

    def test_cost_fail(self):
        passed, _ = self._check({"type": "cost_usd", "value": 0.001}, "output", cost_usd=0.01)
        self.assertFalse(passed)

    def test_unknown_type(self):
        passed, reason = self._check({"type": "unknown_xyz"}, "output")
        self.assertFalse(passed)
        self.assertIn("unknown", reason)


class TestEvalRunCases(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_run_case_success(self):
        """A case with a lenient assertion should pass with mock engine."""
        from aictl.cmd.eval import _run_case
        case = {
            "id": "test-len",
            "prompt": "hello",
            "assertions": [{"type": "min_length", "value": 1}],
        }
        result = _run_case(case, "auto")
        self.assertTrue(result["passed"])
        self.assertIsNone(result["error"])

    def test_run_case_fail_assertion(self):
        from aictl.cmd.eval import _run_case
        case = {
            "id": "test-impossible",
            "prompt": "hello",
            "assertions": [{"type": "contains", "value": "IMPOSSIBLE_TOKEN_XYZ_987654"}],
        }
        result = _run_case(case, "auto")
        self.assertFalse(result["passed"])

    def test_run_case_has_latency(self):
        from aictl.cmd.eval import _run_case
        case = {"id": "latency-test", "prompt": "hello", "assertions": []}
        result = _run_case(case, "auto")
        self.assertGreaterEqual(result["latency_ms"], 0)

    def test_run_case_has_cost(self):
        from aictl.cmd.eval import _run_case
        case = {"id": "cost-test", "prompt": "hello", "assertions": []}
        result = _run_case(case, "auto")
        self.assertIsInstance(result["cost_usd"], float)


class TestEvalCLI(unittest.TestCase):
    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_eval_run_returns_int(self):
        from aictl.cmd.eval import run_eval
        with tempfile.TemporaryDirectory() as td:
            suite_path = Path(td) / "suite.json"
            suite_path.write_text(json.dumps({
                "name": "test", "model": "auto",
                "cases": [{"id": "t1", "prompt": "hello",
                           "assertions": [{"type": "min_length", "value": 1}]}]
            }))
            class FA:
                suite = str(suite_path)
                model = "auto"
                save = None
                json = False
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_eval(FA())
            self.assertIsInstance(rc, int)

    def test_eval_default_shows_help(self):
        from aictl.cmd.eval import run_default
        buf = io.StringIO()
        with redirect_stdout(buf):
            run_default(None)
        self.assertIn("aictl eval", buf.getvalue())

    def test_eval_suite_not_found(self):
        from aictl.cmd.eval import run_eval
        class FA:
            suite = "/nonexistent/path/xyz.json"
            model = "auto"
            save = None
            json = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_eval(FA())
        self.assertNotEqual(rc, 0)


class TestSpecRecommend(unittest.TestCase):
    def test_recommend_llama(self):
        from aictl.cmd.spec import run_recommend
        class FA:
            model = "llama3.1:70b"
            all = False
            json = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_recommend(FA())
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("llama3.2:1b", output)
        self.assertIn("speedup", output.lower())
        self.assertIn("vllm serve", output)

    def test_recommend_json(self):
        from aictl.cmd.spec import run_recommend
        class FA:
            model = "qwen3:32b"
            all = False
            json = True
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_recommend(FA())
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        self.assertIn("speedup", data[0])
        self.assertGreater(data[0]["speedup"], 1.0)

    def test_recommend_all(self):
        from aictl.cmd.spec import run_recommend
        class FA:
            model = None
            all = True
            json = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_recommend(FA())
        self.assertEqual(rc, 0)
        # Must show all pairs
        lines = [l for l in buf.getvalue().splitlines() if "llama" in l.lower() or "qwen" in l.lower()]
        self.assertGreater(len(lines), 5)

    def test_recommend_unknown_model(self):
        from aictl.cmd.spec import run_recommend
        class FA:
            model = "fake-model-xyz-99b"
            all = False
            json = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_recommend(FA())
        self.assertNotEqual(rc, 0)

    def test_bench(self):
        from aictl.cmd.spec import run_bench
        class FA:
            target = "llama3.1:70b"
            draft = "llama3.2:1b"
            gamma = 5
            json = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_bench(FA())
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("speedup", output.lower())
        self.assertIn("vllm serve", output)

    def test_bench_json(self):
        from aictl.cmd.spec import run_bench
        class FA:
            target = "qwen3:32b"
            draft = "qwen3:7b"
            gamma = 4
            json = True
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_bench(FA())
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("estimated_speedup", data)
        self.assertGreater(data["estimated_speedup"], 1.0)

    def test_speedup_math(self):
        """Speedup must be between 1x and 3x."""
        from aictl.cmd.spec import PAIRS
        for p in PAIRS:
            s = p.speedup()
            self.assertGreater(s, 1.0, f"{p.target}: speedup too low")
            self.assertLessEqual(s, 3.1, f"{p.target}: speedup too high (capped at 3x)")

    def test_all_pairs_valid(self):
        """All pairs must have required fields."""
        from aictl.cmd.spec import PAIRS
        for p in PAIRS:
            self.assertGreater(len(p.target), 0)
            self.assertGreater(len(p.draft), 0)
            self.assertGreater(p.acceptance_rate, 0)
            self.assertLessEqual(p.acceptance_rate, 1.0)
            self.assertGreater(p.gamma, 0)
            self.assertGreater(p.target_params_b, p.draft_params_b,
                               f"{p.target}: target must be larger than draft")

    def test_vllm_flags_format(self):
        """vLLM flags must contain required arguments."""
        from aictl.cmd.spec import PAIRS
        p = PAIRS[0]
        flags = p.vllm_flags()
        self.assertIn("--speculative-model", flags)
        self.assertIn("--num-speculative-tokens", flags)
        self.assertIn("vllm serve", flags)


class TestCommandCount(unittest.TestCase):
    def test_eval_and_spec_registered(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        for a in p._actions:
            if hasattr(a, "choices") and a.choices:
                self.assertIn("eval", a.choices)
                self.assertIn("spec", a.choices)
                self.assertGreaterEqual(len(a.choices), 62)
                return
        self.fail("No subparsers found")


if __name__ == "__main__":
    unittest.main()
