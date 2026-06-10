"""Coverage tests for previously-untested cmd modules.

These exercise the run_* entry points of command modules that had no direct
test coverage: convert, completion, recommend, apikey, snapshot. Each test
drives the real handler with an argparse.Namespace and (where state is
involved) an isolated temporary state_dir, asserting on return codes and
--json output rather than terminal formatting.
"""

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _ns(**kw):
    return argparse.Namespace(**kw)


def _capture_json(fn, args):
    """Run a handler that prints JSON, return (rc, parsed_json)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(args)
    out = buf.getvalue().strip()
    parsed = json.loads(out) if out else None
    return rc, parsed


# ─── completion ───────────────────────────────────────────

class TestCompletionCmd(unittest.TestCase):
    def test_bash_completion_emits_script(self):
        from aictl.cmd import completion
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = completion.run(_ns(shell="bash"))
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("_aictl", out)
        self.assertIn("compgen", out)

    def test_zsh_completion_emits_script(self):
        from aictl.cmd import completion
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = completion.run(_ns(shell="zsh"))
        self.assertEqual(rc, 0)
        self.assertIn("aictl", buf.getvalue())

    def test_fish_completion_emits_script(self):
        from aictl.cmd import completion
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = completion.run(_ns(shell="fish"))
        self.assertEqual(rc, 0)
        self.assertIn("aictl", buf.getvalue())

    def test_all_shells_list_core_commands(self):
        from aictl.cmd import completion
        # Every generator should mention the core subcommands
        for gen in (completion._bash_completion, completion._zsh_completion,
                    completion._fish_completion):
            script = gen()
            self.assertIn("recommend", script)
            self.assertIn("deploy", script)


# ─── convert ──────────────────────────────────────────────

class TestConvertCmd(unittest.TestCase):
    def test_bank_no_input_shows_usage(self):
        from aictl.cmd import convert
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = convert.run_bank(_ns(input=None, format="kanjyo-bugyo"))
        self.assertEqual(rc, 0)
        self.assertIn("Usage", buf.getvalue())

    def test_bank_missing_file_returns_1(self):
        from aictl.cmd import convert
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = convert.run_bank(_ns(input="/nonexistent/statement.csv",
                                      format="kanjyo-bugyo"))
        self.assertEqual(rc, 1)

    def test_model_format_safetensors_json(self):
        from aictl.cmd import convert
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "model.safetensors"
            # Minimal SafeTensors: 8-byte LE header length + JSON header
            header = b'{"__metadata__":{}}'
            import struct
            f.write_bytes(struct.pack("<Q", len(header)) + header)
            rc, parsed = _capture_json(
                convert.run_model_format,
                _ns(path=str(f), json=True),
            )
            self.assertEqual(rc, 0)
            self.assertEqual(parsed["format"], "safetensors")

    def test_model_format_empty_dir(self):
        from aictl.cmd import convert
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = convert.run_model_format(_ns(path=d, json=False))
            self.assertEqual(rc, 0)
            self.assertIn("No model files", buf.getvalue())


# ─── recommend ────────────────────────────────────────────

class TestRecommendCmd(unittest.TestCase):
    def test_recommend_json_returns_list(self):
        from aictl.cmd import recommend
        rc, parsed = _capture_json(
            recommend.run,
            _ns(use_case="", top=5, json=True),
        )
        self.assertEqual(rc, 0)
        self.assertIsInstance(parsed, list)
        # Each entry must carry the documented fields
        for entry in parsed:
            self.assertIn("name", entry)
            self.assertIn("runtime", entry)
            self.assertIn("vram_mb", entry)

    def test_recommend_top_limit_respected(self):
        from aictl.cmd import recommend
        rc, parsed = _capture_json(
            recommend.run,
            _ns(use_case="", top=2, json=True),
        )
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(parsed), 2)

    def test_recommend_use_case_filter(self):
        from aictl.cmd import recommend
        rc, parsed = _capture_json(
            recommend.run,
            _ns(use_case="code", top=5, json=True),
        )
        self.assertEqual(rc, 0)
        # If any results come back, they must match the requested use case
        for entry in parsed:
            self.assertEqual(entry["use_case"], "code")


# ─── apikey ───────────────────────────────────────────────

class TestApikeyCmd(unittest.TestCase):
    def test_create_list_revoke_roundtrip(self):
        from aictl.cmd import apikey
        with tempfile.TemporaryDirectory() as d:
            # create
            rc, created = _capture_json(
                apikey.run_create,
                _ns(name="ci-key", rpm=120, expires=0, state_dir=d, json=True),
            )
            self.assertEqual(rc, 0)
            self.assertIn("key", created)
            self.assertTrue(created["key"].startswith("aios-"))
            key_id = created["key_id"]

            # list shows it
            rc, listed = _capture_json(
                apikey.run_list, _ns(state_dir=d, json=True))
            self.assertEqual(rc, 0)
            ids = {k["key_id"] for k in listed}
            self.assertIn(key_id, ids)

            # revoke succeeds
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = apikey.run_revoke(_ns(key_id=key_id, state_dir=d, json=False))
            self.assertEqual(rc, 0)

    def test_revoke_unknown_returns_1(self):
        from aictl.cmd import apikey
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = apikey.run_revoke(
                    _ns(key_id="does-not-exist", state_dir=d, json=False))
            self.assertEqual(rc, 1)

    def test_list_empty_state(self):
        from aictl.cmd import apikey
        with tempfile.TemporaryDirectory() as d:
            rc, listed = _capture_json(
                apikey.run_list, _ns(state_dir=d, json=True))
            self.assertEqual(rc, 0)
            self.assertEqual(listed, [])


# ─── snapshot ─────────────────────────────────────────────

class TestSnapshotCmd(unittest.TestCase):
    def test_create_then_list_and_restore(self):
        from aictl.cmd import snapshot
        with tempfile.TemporaryDirectory() as d:
            rc, created = _capture_json(
                snapshot.run_create,
                _ns(label="t1", state_dir=Path(d), json=True),
            )
            self.assertEqual(rc, 0)
            snap_id = created["id"]

            rc, listed = _capture_json(
                snapshot.run_list, _ns(state_dir=Path(d), json=True))
            self.assertEqual(rc, 0)
            self.assertTrue(any(s["id"] == snap_id for s in listed))

            # restore the just-created snapshot
            rc, restored = _capture_json(
                snapshot.run_restore,
                _ns(id=snap_id, state_dir=Path(d), json=True),
            )
            self.assertEqual(rc, 0)
            self.assertTrue(restored["success"])

    def test_restore_unknown_returns_1(self):
        from aictl.cmd import snapshot
        with tempfile.TemporaryDirectory() as d:
            rc, restored = _capture_json(
                snapshot.run_restore,
                _ns(id="nope", state_dir=Path(d), json=True),
            )
            self.assertEqual(rc, 1)
            self.assertFalse(restored["success"])

    def test_compute_diff_detects_changes(self):
        from aictl.cmd.snapshot import _compute_diff
        a = {"version": "1.5.0", "stacks": [{"name": "s1"}], "models": [1, 2]}
        b = {"version": "1.6.0", "stacks": [{"name": "s2"}], "models": [1]}
        diffs = _compute_diff(a, b)
        joined = " ".join(diffs)
        self.assertIn("1.5.0", joined)
        self.assertIn("Stack removed: s1", diffs)
        self.assertIn("Stack added: s2", diffs)
        self.assertIn("Models: 2 → 1", diffs)

    def test_compute_diff_identical_is_empty(self):
        from aictl.cmd.snapshot import _compute_diff
        a = {"version": "1.6.0", "stacks": [{"name": "s1"}], "models": [1]}
        self.assertEqual(_compute_diff(a, dict(a)), [])


if __name__ == "__main__":
    unittest.main()
