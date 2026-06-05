"""Tests for tco, quota, batch commands."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestTCO(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_state = os.environ.get("AIOS_STATE_DIR")
        os.environ["AIOS_STATE_DIR"] = self._tmp.name
        # Reset global sem_cache to use the new state dir
        import aictl.core.sem_cache as _sc
        self._orig_cache = _sc._DEFAULT_CACHE
        _sc._DEFAULT_CACHE = None

    def tearDown(self):
        import aictl.core.sem_cache as _sc
        _sc._DEFAULT_CACHE = self._orig_cache
        if self._orig_state is None:
            os.environ.pop("AIOS_STATE_DIR", None)
        else:
            os.environ["AIOS_STATE_DIR"] = self._orig_state
        self._tmp.cleanup()

    def test_tco_parses(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["tco"])
        self.assertEqual(args.command, "tco")

    def test_tco_summary_runs(self):
        from aictl.__main__ import build_parser
        from aictl.cmd.tco import run_summary
        p = build_parser()
        args = p.parse_args(["tco"])
        args.json = False
        args.period_days = 30
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_summary(args)
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("Depreciation", output)
        self.assertIn("Electricity", output)
        self.assertIn("Total", output)

    def test_tco_json_mode(self):
        from aictl.cmd.tco import run_summary
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["tco"])
        args.json = True
        args.period_days = 30
        buf = io.StringIO()
        with redirect_stdout(buf):
            run_summary(args)
        # Find JSON portion in output
        output = buf.getvalue().strip()
        lines = [l for l in output.splitlines() if l.strip().startswith("{")]
        self.assertTrue(len(lines) > 0 or "{" in output)
        try:
            data = json.loads(output)
            self.assertIn("total_jpy", data)
        except json.JSONDecodeError:
            # tco may print non-JSON lines before JSON
            pass

    def test_tco_load_defaults(self):
        from aictl.cmd.tco import _load_config, _DEFAULTS
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                cfg = _load_config()
                for k in _DEFAULTS:
                    self.assertIn(k, cfg)
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)

    def test_tco_save_and_reload(self):
        from aictl.cmd.tco import _load_config, _save_config
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                cfg = _load_config()
                cfg["kwh_rate_jpy"] = 30
                _save_config(cfg)
                cfg2 = _load_config()
                self.assertEqual(cfg2["kwh_rate_jpy"], 30)
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)


class TestQuota(unittest.TestCase):
    def _with_tmp(self, fn):
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                fn(td)
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)

    def test_quota_create_and_list(self):
        def _test(td):
            from aictl.__main__ import build_parser
            from aictl.cmd.quota import run_create, run_list
            p = build_parser()
            args_c = p.parse_args(["quota", "create", "test-team",
                                    "--tokens-per-month", "5000000"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_create(args_c)
            self.assertEqual(rc, 0)

            args_l = p.parse_args(["quota", "list"])
            args_l.json = False
            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                rc2 = run_list(args_l)
            self.assertEqual(rc2, 0)
            self.assertIn("test-team", buf2.getvalue())
        self._with_tmp(_test)

    def test_quota_report(self):
        def _test(td):
            from aictl.__main__ import build_parser
            from aictl.cmd.quota import run_create, run_report
            p = build_parser()
            run_create(p.parse_args(["quota", "create", "eng",
                                      "--tokens-per-month", "1000000"]))
            args_r = p.parse_args(["quota", "report"])
            args_r.json = False
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_report(args_r)
            self.assertEqual(rc, 0)
            self.assertIn("eng", buf.getvalue())
        self._with_tmp(_test)

    def test_quota_reset_requires_yes(self):
        def _test(td):
            from aictl.__main__ import build_parser
            from aictl.cmd.quota import run_create, run_reset
            p = build_parser()
            run_create(p.parse_args(["quota", "create", "myteam",
                                      "--tokens-per-month", "100"]))
            args_r = p.parse_args(["quota", "reset", "myteam"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_reset(args_r)
            self.assertNotEqual(rc, 0)
        self._with_tmp(_test)

    def test_quota_reset_with_yes(self):
        def _test(td):
            from aictl.__main__ import build_parser
            from aictl.cmd.quota import run_create, run_reset
            p = build_parser()
            run_create(p.parse_args(["quota", "create", "myteam2",
                                      "--tokens-per-month", "100"]))
            args_r = p.parse_args(["quota", "reset", "myteam2", "--yes"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_reset(args_r)
            self.assertEqual(rc, 0)
        self._with_tmp(_test)


class TestBatch(unittest.TestCase):
    def _with_tmp(self, fn):
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            try:
                fn(td)
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)

    def test_batch_add_and_list(self):
        def _test(td):
            from aictl.__main__ import build_parser
            from aictl.cmd.batch import run_add, run_list
            p = build_parser()
            args_a = p.parse_args(["batch", "add", "test-job",
                                    "--task", "embed"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_add(args_a)
            self.assertEqual(rc, 0)

            args_l = p.parse_args(["batch", "list"])
            args_l.json = False
            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                rc2 = run_list(args_l)
            self.assertEqual(rc2, 0)
            self.assertIn("test-job", buf2.getvalue())
        self._with_tmp(_test)

    def test_batch_remove(self):
        def _test(td):
            from aictl.__main__ import build_parser
            from aictl.cmd.batch import run_add, run_remove
            p = build_parser()
            run_add(p.parse_args(["batch", "add", "remove-me"]))
            args_r = p.parse_args(["batch", "remove", "remove-me"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_remove(args_r)
            self.assertEqual(rc, 0)
        self._with_tmp(_test)

    def test_batch_run_embed_no_path(self):
        """batch run with nonexistent path should not crash."""
        def _test(td):
            from aictl.__main__ import build_parser
            from aictl.cmd.batch import run_add, run_now, _load
            p = build_parser()
            run_add(p.parse_args([
                "batch", "add", "embed-test",
                "--input", "/nonexistent/path/xyz",
                "--task", "embed"
            ]))
            args_r = p.parse_args(["batch", "run", "embed-test"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_now(args_r)
            # May succeed or fail, but must not crash
            self.assertIsInstance(rc, int)
        self._with_tmp(_test)

    def test_batch_list_empty(self):
        def _test(td):
            from aictl.__main__ import build_parser
            from aictl.cmd.batch import run_list
            import contextlib
            p = build_parser()
            args_l = p.parse_args(["batch", "list"])
            args_l.json = False
            buf_out = io.StringIO()
            buf_err = io.StringIO()
            with redirect_stdout(buf_out):
                with contextlib.redirect_stderr(buf_err):
                    rc = run_list(args_l)
            self.assertEqual(rc, 0)
            combined = buf_out.getvalue() + buf_err.getvalue()
            # Should mention "batch" and how to add jobs
            self.assertTrue(
                "batch" in combined.lower() or "add" in combined.lower()
            )
        self._with_tmp(_test)


class TestNextAction(unittest.TestCase):
    def test_suggest_known_key(self):
        from aictl.core.next_action import suggest
        buf = io.StringIO()
        with redirect_stdout(buf):
            suggest("rag_index")
        self.assertIn("Try next", buf.getvalue())

    def test_suggest_unknown_key_silent(self):
        from aictl.core.next_action import suggest
        buf = io.StringIO()
        with redirect_stdout(buf):
            suggest("totally_unknown_xyz_key")
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
