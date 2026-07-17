"""Coverage tests for cmd modules (batch 5): model command."""

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


def _capture(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(args)
    return rc, buf.getvalue()


def _capture_json(fn, args):
    rc, out = _capture(fn, args)
    out = out.strip()
    return rc, (json.loads(out) if out else None)


class TestModelCmd(unittest.TestCase):
    def test_list_empty_returns_empty_list(self):
        from aictl.cmd import model
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(model.run_list, _ns(state_dir=Path(d), json=True))
        self.assertEqual(rc, 0)
        self.assertEqual(parsed, [])

    def test_register_creates_entry_with_id(self):
        from aictl.cmd import model
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                model.run_register,
                _ns(state_dir=Path(d), name="llama3:8b", digest="", fmt="gguf",
                    signed=False, json=True),
            )
        self.assertEqual(rc, 0)
        self.assertIn("id", parsed)
        self.assertIn("name", parsed)
        self.assertEqual(parsed["name"], "llama3:8b")
        self.assertTrue(len(parsed["id"]) > 0)

    def test_register_then_list_roundtrip(self):
        from aictl.cmd import model
        with tempfile.TemporaryDirectory() as d:
            _, created = _capture_json(
                model.run_register,
                _ns(state_dir=Path(d), name="phi4:14b", digest="sha256:abc",
                    fmt="safetensors", signed=True, json=True),
            )
            rc, listed = _capture_json(
                model.run_list, _ns(state_dir=Path(d), json=True))
        self.assertEqual(rc, 0)
        self.assertIsInstance(listed, list)
        self.assertEqual(len(listed), 1)
        entry = listed[0]
        self.assertEqual(entry["id"], created["id"])

    def test_register_multiple_models(self):
        from aictl.cmd import model
        with tempfile.TemporaryDirectory() as d:
            for name in ["llama3:8b", "mistral:7b", "phi4:14b"]:
                _capture_json(
                    model.run_register,
                    _ns(state_dir=Path(d), name=name, digest="", fmt="gguf",
                        signed=False, json=True),
                )
            rc, listed = _capture_json(
                model.run_list, _ns(state_dir=Path(d), json=True))
        self.assertEqual(rc, 0)
        self.assertEqual(len(listed), 3)

    def test_cache_status_returns_0(self):
        from aictl.cmd import model
        with tempfile.TemporaryDirectory() as d:
            rc, out = _capture(
                model.run_cache, _ns(state_dir=Path(d), clean=False, days=30, json=False))
        self.assertEqual(rc, 0)
        self.assertIn("cache", out.lower())

    def test_cache_clean_empty_returns_0(self):
        from aictl.cmd import model
        with tempfile.TemporaryDirectory() as d:
            rc, out = _capture(
                model.run_cache, _ns(state_dir=Path(d), clean=True, days=30, json=False))
        self.assertEqual(rc, 0)

    def test_each_registration_gets_unique_id(self):
        from aictl.cmd import model
        ids = []
        for _ in range(5):
            with tempfile.TemporaryDirectory() as d:
                _, parsed = _capture_json(
                    model.run_register,
                    _ns(state_dir=Path(d), name="m", digest="", fmt="gguf",
                        signed=False, json=True),
                )
                ids.append(parsed["id"])
        self.assertEqual(len(set(ids)), 5, "Each registration must generate a unique ID")


if __name__ == "__main__":
    unittest.main()
