"""Pass 158: core config/metering loaders must validate a dict root (not crash).

Extends the V7 sweep (Passes 151-156) to two central `aictl/core` loaders that
were missed because their non-dict-root failure wasn't a plain KeyError/
AttributeError from a bracket access — it came from `.get()`/`.items()` called
on a list, which the narrow `except (json.JSONDecodeError, KeyError/OSError)`
clauses did not catch.

  - `aictl.core.config.load_config` is called by nearly every command (it's the
    central config loader). A list-rooted config.json passes `"engines" in data`
    harmlessly (False), but the very next line, `data.get("trust_policy", ...)`,
    raises AttributeError ('list' object has no attribute 'get') — uncaught,
    surfaced as "report a bug" for a corrupt config.json on almost any command.

  - `aictl.core.metering.TokenMeter._load_buckets` calls `data.items()` after
    parsing; a list-rooted metering.json raises the same AttributeError.

Fix: both guard `isinstance(data, dict)` before the field-by-field parse and
degrade to the default (Config() / {}) on a non-object root, matching the
established V7 pattern. Their except clauses also now catch AttributeError/
TypeError as defense-in-depth for any other shape mismatch.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class TestLoadConfigRoot(unittest.TestCase):
    def test_list_root_degrades_to_defaults(self):
        from aictl.core.config import load_config, Config
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text("[1, 2, 3]")
        c = load_config(d)
        self.assertEqual(c.trust_policy, Config().trust_policy)
        self.assertEqual(c.log_level, Config().log_level)

    def test_scalar_root_degrades_to_defaults(self):
        from aictl.core.config import load_config
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text("42")
        c = load_config(d)   # must not raise
        self.assertEqual(c.trust_policy, "warn")

    def test_string_root_degrades(self):
        from aictl.core.config import load_config
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text('"corrupt"')
        load_config(d)   # must not raise

    def test_valid_config_still_loads(self):
        from aictl.core.config import load_config
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text(json.dumps({"trust_policy": "enforce",
                                                    "log_level": "debug"}))
        c = load_config(d)
        self.assertEqual(c.trust_policy, "enforce")
        self.assertEqual(c.log_level, "debug")

    def test_malformed_json_still_graceful(self):
        from aictl.core.config import load_config
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text("{not json")
        load_config(d)   # pre-existing graceful path must remain graceful


class TestConfigShowCliNoCrash(unittest.TestCase):
    def test_config_show_survives_list_rooted_file(self):
        import argparse
        import contextlib
        import io
        from aictl.cmd.config import run_show
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text("[1, 2, 3]")
        args = argparse.Namespace(state_dir=str(d), json=False)
        out, errbuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            with contextlib.redirect_stderr(errbuf):
                code = run_show(args)
        self.assertEqual(code, 0)
        self.assertNotIn("report", (out.getvalue() + errbuf.getvalue()).lower())


class TestMeteringLoadBucketsRoot(unittest.TestCase):
    def test_list_root_degrades_to_empty(self):
        from aictl.core.metering import TokenMeter
        d = Path(tempfile.mkdtemp())
        (d / "metering.json").write_text("[1, 2, 3]")
        m = TokenMeter(d)
        self.assertEqual(m._load_buckets(), {})

    def test_scalar_root_degrades(self):
        from aictl.core.metering import TokenMeter
        d = Path(tempfile.mkdtemp())
        (d / "metering.json").write_text("42")
        m = TokenMeter(d)
        self.assertEqual(m._load_buckets(), {})

    def test_valid_buckets_still_load(self):
        from aictl.core.metering import TokenMeter
        d = Path(tempfile.mkdtemp())
        m = TokenMeter(d)
        m.record("teamX", "llama3", 10, 5)
        loaded = m._load_buckets()
        self.assertIn("teamX", loaded)
        self.assertEqual(loaded["teamX"].total_tokens, 15)


if __name__ == "__main__":
    unittest.main()
