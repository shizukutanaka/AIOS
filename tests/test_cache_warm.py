"""Tests for aictl cache warm subcommand."""

import argparse
import json
import pathlib
import tempfile
import unittest


class TestCacheWarmRegistered(unittest.TestCase):
    """warm subcommand must be registered in cache_cmd.register()."""

    def _make_parser(self):
        from aictl.cmd.cache_cmd import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_warm_parser_exists(self):
        parser = self._make_parser()
        args = parser.parse_args(["cache", "warm", "prompts.jsonl"])
        self.assertEqual(args.func.__name__, "run_warm")

    def test_warm_dry_run_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["cache", "warm", "f.jsonl", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_warm_dry_run_default_false(self):
        parser = self._make_parser()
        args = parser.parse_args(["cache", "warm", "f.jsonl"])
        self.assertFalse(args.dry_run)


class TestCacheWarmLogic(unittest.TestCase):
    """run_warm() parses JSONL and stores entries in the cache."""

    def _write_jsonl(self, tmpdir, lines):
        path = pathlib.Path(tmpdir) / "prompts.jsonl"
        path.write_text("\n".join(json.dumps(l) for l in lines))
        return str(path)

    def _run(self, tmpdir, lines, dry_run=False, extra_args=None):
        from unittest.mock import patch, MagicMock
        from aictl.cmd.cache_cmd import run_warm

        captured = []

        def fake_print_json(data):
            captured.append(data)

        file_path = self._write_jsonl(tmpdir, lines)
        mock_cache = MagicMock()
        args = argparse.Namespace(
            file=file_path,
            dry_run=dry_run,
            json=True,
        )
        with patch("aictl.core.sem_cache.get_default_cache", return_value=mock_cache), \
             patch("aictl.cmd.cache_cmd.print_json", side_effect=fake_print_json):
            ret = run_warm(args)
        return ret, captured[0] if captured else None, mock_cache

    def test_valid_entries_stored(self):
        entries = [
            {"prompt": "What is 2+2?", "response": "4", "model": "llama3.2:1b"},
            {"prompt": "What is the capital of France?", "response": "Paris", "model": "llama3.2:1b"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            ret, data, mock_cache = self._run(tmpdir, entries)
        self.assertEqual(ret, 0)
        self.assertEqual(data["stored"], 2)
        self.assertEqual(data["skipped"], 0)
        self.assertEqual(data["parse_errors"], 0)
        self.assertEqual(mock_cache.store.call_count, 2)

    def test_missing_required_field_counted_as_error(self):
        entries = [
            {"prompt": "Hello?", "response": "Hi!", "model": "m"},
            {"prompt": "Missing response field", "model": "m"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            ret, data, _ = self._run(tmpdir, entries)
        # One entry valid, one has parse error (missing 'response')
        self.assertEqual(data["stored"], 1)
        self.assertEqual(data["parse_errors"], 1)

    def test_dry_run_does_not_call_store(self):
        entries = [{"prompt": "Q?", "response": "A.", "model": "m"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            ret, data, mock_cache = self._run(tmpdir, entries, dry_run=True)
        self.assertEqual(ret, 0)
        self.assertEqual(data["valid"], 1)
        self.assertTrue(data["dry_run"])
        mock_cache.store.assert_not_called()

    def test_tokens_passed_to_store(self):
        entries = [{"prompt": "Q?", "response": "A.", "model": "m", "tokens": 42}]
        with tempfile.TemporaryDirectory() as tmpdir:
            _, _, mock_cache = self._run(tmpdir, entries)
        call_kwargs = mock_cache.store.call_args
        # tokens=42 should have been passed
        self.assertEqual(call_kwargs.kwargs.get("tokens") or
                         (call_kwargs.args[3] if len(call_kwargs.args) > 3 else None), 42)

    def test_file_not_found_returns_1(self):
        from aictl.cmd.cache_cmd import run_warm
        args = argparse.Namespace(file="/nonexistent/path.jsonl", dry_run=False, json=False)
        ret = run_warm(args)
        self.assertEqual(ret, 1)

    def test_empty_file_returns_1(self):
        entries = []
        with tempfile.TemporaryDirectory() as tmpdir:
            ret, _, _ = self._run(tmpdir, entries)
        self.assertEqual(ret, 1)

    def test_blank_lines_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "prompts.jsonl"
            path.write_text(
                json.dumps({"prompt": "Q", "response": "A", "model": "m"}) + "\n"
                "\n"
                "   \n"
            )
            from unittest.mock import patch, MagicMock
            from aictl.cmd.cache_cmd import run_warm
            captured = []
            mock_cache = MagicMock()
            args = argparse.Namespace(file=str(path), dry_run=False, json=True)
            with patch("aictl.core.sem_cache.get_default_cache", return_value=mock_cache), \
                 patch("aictl.cmd.cache_cmd.print_json", side_effect=lambda d: captured.append(d)):
                run_warm(args)
        self.assertEqual(captured[0]["stored"], 1)
        self.assertEqual(captured[0]["parse_errors"], 0)


if __name__ == "__main__":
    unittest.main()
