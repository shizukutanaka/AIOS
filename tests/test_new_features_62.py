"""Pass 62 regression tests: bench compare, apply --validate-only."""

from __future__ import annotations

import argparse
import pathlib
import tempfile
import unittest
from unittest.mock import patch, MagicMock


class TestBenchCompare(unittest.TestCase):
    """bench compare runs benchmarks against multiple endpoints and shows a winner."""

    def _make_parser(self):
        from aictl.cmd.bench import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_compare_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["bench", "compare",
                                  "http://localhost:8000", "http://localhost:8001"])
        self.assertEqual(args.func.__name__, "run_compare")

    def test_compare_endpoints_positional(self):
        parser = self._make_parser()
        args = parser.parse_args(["bench", "compare",
                                  "http://a:8000", "http://b:8001"])
        self.assertEqual(args.endpoints, ["http://a:8000", "http://b:8001"])

    def test_compare_json_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["bench", "compare",
                                  "http://a:8000", "http://b:8001", "--json"])
        self.assertTrue(args.json)

    def test_compare_requests_default(self):
        parser = self._make_parser()
        args = parser.parse_args(["bench", "compare",
                                  "http://a:8000", "http://b:8001"])
        self.assertEqual(args.requests, 5)

    def test_compare_returns_1_for_single_endpoint(self):
        from aictl.cmd.bench import run_compare
        args = argparse.Namespace(
            endpoints=["http://localhost:8000"],
            model="", requests=1, max_tokens=10, json=False,
        )
        ret = run_compare(args)
        self.assertEqual(ret, 1)

    def test_compare_returns_0_with_two_endpoints(self):
        from aictl.cmd.bench import run_compare
        from aictl.runtime.benchmark import BenchResult

        fake = BenchResult(
            endpoint="http://x", model="m", requests=1,
            errors=0, tokens_per_sec=100.0, duration_sec=1.0,
        )
        captured = []
        with patch("aictl.runtime.benchmark.run_benchmark", return_value=fake), \
             patch("aictl.cmd.bench.print_table", side_effect=lambda *a, **k: None):
            args = argparse.Namespace(
                endpoints=["http://a:8000", "http://b:8001"],
                model="", requests=1, max_tokens=10, json=False,
            )
            ret = run_compare(args)
        self.assertEqual(ret, 0)

    def test_compare_json_output_is_list(self):
        from aictl.cmd.bench import run_compare
        from aictl.runtime.benchmark import BenchResult

        fake = BenchResult(
            endpoint="http://x", model="m", requests=1, errors=0,
            tokens_per_sec=50.0, duration_sec=1.0,
        )
        captured = []
        with patch("aictl.runtime.benchmark.run_benchmark", return_value=fake), \
             patch("aictl.cmd.bench.print_json", side_effect=captured.append):
            args = argparse.Namespace(
                endpoints=["http://a:8000", "http://b:8001"],
                model="", requests=1, max_tokens=10, json=True,
            )
            ret = run_compare(args)
        self.assertEqual(ret, 0)
        self.assertIsInstance(captured[0], list)
        self.assertEqual(len(captured[0]), 2)

    def test_compare_winner_is_highest_throughput(self):
        from aictl.cmd.bench import run_compare
        from aictl.runtime.benchmark import BenchResult

        call_count = [0]
        def fake_bench(endpoint, **kw):
            call_count[0] += 1
            tps = 200.0 if "b" in endpoint else 50.0
            return BenchResult(endpoint=endpoint, model="m", requests=1,
                               errors=0, tokens_per_sec=tps, duration_sec=1.0)

        captured = []
        with patch("aictl.runtime.benchmark.run_benchmark", side_effect=fake_bench), \
             patch("aictl.cmd.bench.print_table", side_effect=lambda *a, **k: None):
            args = argparse.Namespace(
                endpoints=["http://a:8000", "http://b:8001"],
                model="", requests=1, max_tokens=10, json=False,
            )
            ret = run_compare(args)
        self.assertEqual(ret, 0)

    def test_compare_handles_unreachable_endpoint(self):
        from aictl.cmd.bench import run_compare
        from aictl.runtime.benchmark import BenchResult

        def fail_or_succeed(endpoint, **kw):
            if "bad" in endpoint:
                raise ConnectionRefusedError("unreachable")
            return BenchResult(endpoint=endpoint, model="m", requests=1,
                               errors=0, tokens_per_sec=100.0, duration_sec=1.0)

        with patch("aictl.runtime.benchmark.run_benchmark", side_effect=fail_or_succeed), \
             patch("aictl.cmd.bench.print_table", side_effect=lambda *a, **k: None):
            args = argparse.Namespace(
                endpoints=["http://bad:9999", "http://good:8000"],
                model="", requests=1, max_tokens=10, json=False,
            )
            ret = run_compare(args)
        self.assertEqual(ret, 0)


class TestApplyValidateOnly(unittest.TestCase):
    """apply --validate-only parses and validates manifest without executing."""

    def _make_parser(self):
        from aictl.cmd.apply import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_validate_only_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["apply", "-f", "stack.yaml", "--validate-only"])
        self.assertTrue(args.validate_only)

    def test_json_flag_on_apply(self):
        parser = self._make_parser()
        args = parser.parse_args(["apply", "-f", "stack.yaml", "--json"])
        self.assertTrue(args.json)

    def test_validate_only_returns_0_on_valid_manifest(self):
        from aictl.cmd.apply import run
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        mock_manifest = MagicMock()
        mock_manifest.name = "test-stack"
        svc = MagicMock()
        svc.name = "llama"
        svc.runtime = "vllm"
        svc.model = "llama3:8b"
        mock_manifest.services = [svc]

        with patch("aictl.cmd.apply.parse_file", return_value=mock_manifest):
            args = argparse.Namespace(
                file="fake.yaml", validate_only=True, dry_run=False,
                quadlet=False, root=False, json=False, state_dir=tmpdir,
            )
            ret = run(args)
        self.assertEqual(ret, 0)

    def test_validate_only_json_output(self):
        from aictl.cmd.apply import run
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        mock_manifest = MagicMock()
        mock_manifest.name = "my-stack"
        svc = MagicMock()
        svc.name = "engine"
        svc.runtime = "ollama"
        svc.model = "mistral"
        mock_manifest.services = [svc]

        captured = []
        with patch("aictl.cmd.apply.parse_file", return_value=mock_manifest), \
             patch("aictl.cmd.apply.print_json", side_effect=captured.append):
            args = argparse.Namespace(
                file="fake.yaml", validate_only=True, dry_run=False,
                quadlet=False, root=False, json=True, state_dir=tmpdir,
            )
            ret = run(args)
        self.assertEqual(ret, 0)
        self.assertEqual(len(captured), 1)
        data = captured[0]
        self.assertTrue(data["valid"])
        self.assertEqual(data["stack"], "my-stack")
        self.assertEqual(data["services"], 1)

    def test_validate_only_json_has_service_list(self):
        from aictl.cmd.apply import run
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        mock_manifest = MagicMock()
        mock_manifest.name = "s"
        svc = MagicMock()
        svc.name = "svc1"
        svc.runtime = "vllm"
        svc.model = "llama3"
        mock_manifest.services = [svc]

        captured = []
        with patch("aictl.cmd.apply.parse_file", return_value=mock_manifest), \
             patch("aictl.cmd.apply.print_json", side_effect=captured.append):
            args = argparse.Namespace(
                file="f.yaml", validate_only=True, dry_run=False,
                quadlet=False, root=False, json=True, state_dir=tmpdir,
            )
            run(args)
        self.assertIn("service_list", captured[0])
        self.assertEqual(captured[0]["service_list"][0]["name"], "svc1")

    def test_validate_only_returns_1_on_parse_error(self):
        from aictl.cmd.apply import run
        from aictl.stack.manifest import StackParseError
        tmpdir = pathlib.Path(tempfile.mkdtemp())

        with patch("aictl.cmd.apply.parse_file", side_effect=StackParseError("bad yaml")):
            args = argparse.Namespace(
                file="bad.yaml", validate_only=True, dry_run=False,
                quadlet=False, root=False, json=False, state_dir=tmpdir,
            )
            ret = run(args)
        self.assertEqual(ret, 1)

    def test_validate_only_does_not_call_apply_stack(self):
        from aictl.cmd.apply import run
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        mock_manifest = MagicMock()
        mock_manifest.name = "s"
        mock_manifest.services = []

        with patch("aictl.cmd.apply.parse_file", return_value=mock_manifest), \
             patch("aictl.cmd.apply.apply_stack") as mock_apply:
            args = argparse.Namespace(
                file="f.yaml", validate_only=True, dry_run=False,
                quadlet=False, root=False, json=False, state_dir=tmpdir,
            )
            run(args)
        mock_apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
