"""Pass 159: cloud-fallback config must actually work as documented, and be safe.

長所短所改善点 follow-up to Pass 158. `aictl/runtime/fallback.py`'s own docstring
documents the way to configure cloud fallback:

    aictl config set fallback.provider openrouter
    aictl config set fallback.api_key sk-or-xxx
    aictl config set fallback.model meta-llama/llama-3.1-8b-instruct
    Security: API keys stored in ~/.aios/config.json (chmod 600)

But `Config` had no `fallback` field, so every one of those commands failed with
"Unknown key: fallback.provider" — the entire cloud-fallback CLI configuration
path was non-functional (only environment variables worked). And the docstring's
security claim ("chmod 600") was aspirational, not implemented anywhere.

Fix:
  - `FallbackSettings` dataclass + `Config.fallback` field, wired through
    `load_config`/`_dict_to_config`, so `config set fallback.*` now works.
  - `config show`/`config diff` redact `fallback.api_key` in bulk dumps (never
    plaintext-dump a credential just from routine inspection); `config get
    fallback.api_key` (an explicit single-key ask) is intentionally NOT
    redacted, matching the `aws configure get` convention.
  - `config export` now writes atomically with mode=0o600 (closing the same
    world-readable-secret gap fixed for api_keys.json in Pass 157) and warns
    the user when the exported file carries the real key; it stays UNredacted
    so the documented export -> import round-trip keeps working.
  - `runtime.fallback.load_fallback_config` now delegates to the single,
    V7-hardened `core.config.load_config` instead of re-parsing config.json —
    removing duplicate code that had the exact same non-dict-root crash class
    as every other loader fixed under V7.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path


def _cli(argv):
    from aictl.cmd import config as config_cmd
    p = argparse.ArgumentParser(prog="aictl")
    p.add_argument("--state-dir", default=None)
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers()
    config_cmd.register(sub)
    ns = p.parse_args(argv)
    out, errbuf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out):
        with contextlib.redirect_stderr(errbuf):
            code = ns.func(ns)
    return code, out.getvalue() + errbuf.getvalue()


class TestFallbackConfigSetWorks(unittest.TestCase):
    def test_documented_commands_succeed(self):
        d = tempfile.mkdtemp()
        for key, value in [("fallback.provider", "openrouter"),
                          ("fallback.api_key", "sk-or-test"),
                          ("fallback.enabled", "true"),
                          ("fallback.model", "meta-llama/llama-3.1-8b-instruct")]:
            code, text = _cli(["--state-dir", d, "config", "set", key, value])
            self.assertEqual(code, 0, text)
            self.assertNotIn("Unknown key", text)

    def test_values_persist(self):
        d = tempfile.mkdtemp()
        _cli(["--state-dir", d, "config", "set", "fallback.provider", "groq"])
        code, text = _cli(["--state-dir", d, "config", "get", "fallback.provider"])
        self.assertEqual(text.strip(), "groq")


class TestFallbackRedaction(unittest.TestCase):
    def _seed(self, d):
        _cli(["--state-dir", d, "config", "set", "fallback.api_key", "sk-secret-abc"])

    def test_show_redacts_api_key(self):
        d = tempfile.mkdtemp()
        self._seed(d)
        code, text = _cli(["--state-dir", d, "config", "show"])
        self.assertNotIn("sk-secret-abc", text)
        self.assertIn("REDACTED", text)

    def test_show_json_redacts_api_key(self):
        d = tempfile.mkdtemp()
        self._seed(d)
        code, text = _cli(["--state-dir", d, "--json", "config", "show"])
        data = json.loads(text)
        self.assertNotEqual(data["fallback"]["api_key"], "sk-secret-abc")

    def test_diff_redacts_api_key(self):
        d = tempfile.mkdtemp()
        self._seed(d)
        code, text = _cli(["--state-dir", d, "config", "diff"])
        self.assertNotIn("sk-secret-abc", text)

    def test_get_explicit_key_not_redacted(self):
        # Explicit single-key request must still return the real value.
        d = tempfile.mkdtemp()
        self._seed(d)
        code, text = _cli(["--state-dir", d, "config", "get", "fallback.api_key"])
        self.assertEqual(text.strip(), "sk-secret-abc")

    def test_no_redaction_noise_when_key_unset(self):
        d = tempfile.mkdtemp()
        code, text = _cli(["--state-dir", d, "config", "show"])
        self.assertNotIn("REDACTED", text)


class TestFallbackExportSecurity(unittest.TestCase):
    def test_export_is_0600(self):
        d = tempfile.mkdtemp()
        _cli(["--state-dir", d, "config", "set", "fallback.api_key", "sk-secret"])
        out = str(Path(d) / "exported.json")
        _cli(["--state-dir", d, "config", "export", "--output", out])
        mode = stat.S_IMODE(Path(out).stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_export_warns_when_secret_present(self):
        d = tempfile.mkdtemp()
        _cli(["--state-dir", d, "config", "set", "fallback.api_key", "sk-secret"])
        out = str(Path(d) / "exported.json")
        code, text = _cli(["--state-dir", d, "config", "export", "--output", out])
        self.assertIn("credential", text.lower())

    def test_export_unredacted_for_round_trip(self):
        # Export/import must still functionally move the real key.
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        _cli(["--state-dir", d1, "config", "set", "fallback.api_key", "sk-roundtrip"])
        out = str(Path(d1) / "exported.json")
        _cli(["--state-dir", d1, "config", "export", "--output", out])
        _cli(["--state-dir", d2, "config", "import", out])
        code, text = _cli(["--state-dir", d2, "config", "get", "fallback.api_key"])
        self.assertEqual(text.strip(), "sk-roundtrip")

    def test_no_secret_no_warning(self):
        d = tempfile.mkdtemp()
        out = str(Path(d) / "exported.json")
        code, text = _cli(["--state-dir", d, "config", "export", "--output", out])
        self.assertNotIn("credential", text.lower())


class TestLoadFallbackConfigDelegation(unittest.TestCase):
    def test_reads_real_key_for_runtime_use(self):
        # The runtime consumer must see the REAL key, not a redacted one.
        d = tempfile.mkdtemp()
        _cli(["--state-dir", d, "config", "set", "fallback.provider", "openrouter"])
        _cli(["--state-dir", d, "config", "set", "fallback.api_key", "sk-real"])
        _cli(["--state-dir", d, "config", "set", "fallback.enabled", "true"])
        from aictl.runtime.fallback import load_fallback_config
        fb = load_fallback_config(d)
        self.assertTrue(fb.enabled)
        self.assertEqual(fb.provider, "openrouter")
        self.assertEqual(fb.api_key, "sk-real")

    def test_non_dict_root_no_longer_crashes(self):
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text("[1, 2, 3]")
        from aictl.runtime.fallback import load_fallback_config
        fb = load_fallback_config(str(d))   # must not raise
        self.assertFalse(fb.enabled)

    def test_no_config_file_returns_defaults(self):
        d = tempfile.mkdtemp()
        from aictl.runtime.fallback import load_fallback_config
        fb = load_fallback_config(d)
        self.assertFalse(fb.enabled)
        self.assertEqual(fb.api_key, "")


if __name__ == "__main__":
    unittest.main()
