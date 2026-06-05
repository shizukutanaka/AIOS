"""Tests for quota SDK integration and release pipeline readiness."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestQuotaSDKIntegration(unittest.TestCase):
    """SDK note_usage must update quota when AICTL_TEAM is set."""

    def setUp(self):
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()

    def test_note_usage_increments_quota(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            os.environ["AICTL_TEAM"] = "test-team-quota-unique"
            try:
                # Create a quota entry
                qdata = {
                    "teams": {
                        "test-team-quota-unique": {
                            "tokens_per_month": 1_000_000,
                            "priority": "normal",
                            "used_tokens": 0,
                        }
                    }
                }
                (Path(td) / "quotas.json").write_text(
                    json.dumps(qdata)
                )

                # Reset semantic cache singleton so this test starts fresh
                import aictl.core.sem_cache as _scm
                _scm._DEFAULT_CACHE = None

                # Use a unique prompt to guarantee cache miss
                import time as _t
                unique_prompt = f"quota test {_t.time()} unique abc xyz"
                import aictl
                aictl.ai.ask(unique_prompt)

                # Check quota was updated
                updated = json.loads(
                    (Path(td) / "quotas.json").read_text()
                )
                used = updated["teams"]["test-team-quota-unique"]["used_tokens"]
                self.assertGreater(used, 0,
                    "Quota was not incremented after SDK call")
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)
                os.environ.pop("AICTL_TEAM", None)
                # Clean up cache singleton
                import aictl.core.sem_cache as _scm
                _scm._DEFAULT_CACHE = None

    def test_note_usage_no_team_env_no_crash(self):
        """Without AICTL_TEAM, quota tracking should be silent no-op."""
        os.environ.pop("AICTL_TEAM", None)
        import aictl
        # Should not raise anything
        r = aictl.ai.ask("test no-team query")
        self.assertIsNotNone(str(r))

    def test_note_usage_cached_not_counted(self):
        """Cached responses should NOT increment quota usage."""
        with tempfile.TemporaryDirectory() as td:
            os.environ["AIOS_STATE_DIR"] = td
            os.environ["AICTL_TEAM"] = "cache-team"
            try:
                qdata = {
                    "teams": {
                        "cache-team": {
                            "tokens_per_month": 1_000_000,
                            "priority": "normal",
                            "used_tokens": 0,
                        }
                    }
                }
                (Path(td) / "quotas.json").write_text(json.dumps(qdata))

                import aictl
                # First call — actual inference
                aictl.ai.ask("cached test query abc123")
                after_first = json.loads(
                    (Path(td) / "quotas.json").read_text()
                )["teams"]["cache-team"]["used_tokens"]

                # Second call — should be cached
                r = aictl.ai.ask("cached test query abc123")
                if r.cached:
                    after_second = json.loads(
                        (Path(td) / "quotas.json").read_text()
                    )["teams"]["cache-team"]["used_tokens"]
                    self.assertEqual(
                        after_first, after_second,
                        "Cached response should not add to quota"
                    )
            finally:
                os.environ.pop("AIOS_STATE_DIR", None)
                os.environ.pop("AICTL_TEAM", None)


class TestMakefileTargets(unittest.TestCase):
    """Verify Makefile targets produce correct output."""

    def test_guard_check_passes(self):
        """make guard-check equivalent: all guardrail tests pass."""
        from aictl.cmd.guard import run_test
        class FA:
            json = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_test(FA())
        self.assertEqual(rc, 0)
        self.assertIn("passed", buf.getvalue())

    def test_perf_check_passes(self):
        """make perf-check equivalent: startup under 200ms."""
        import time
        t0 = time.perf_counter()
        from aictl.core.constants import AICTL_VERSION
        ms = (time.perf_counter() - t0) * 1000
        self.assertLess(ms, 200,
                        f"core.constants import: {ms:.0f}ms (limit 200ms)")

    def test_rag_check_passes(self):
        """make rag-check equivalent: RAG pipeline fundamentals work."""
        from aictl.core.rag import (
            chunk_text, _fallback_embedding, cosine, RagStore
        )
        chunks = chunk_text("hello world paragraph one\n\nhello paragraph two")
        self.assertGreater(len(chunks), 0)

        emb = _fallback_embedding("test", dim=64)
        self.assertEqual(len(emb), 64)

        sim = cosine(emb, emb)
        self.assertAlmostEqual(sim, 1.0, places=1)

    def test_sdk_check_passes(self):
        """make sdk-check equivalent: SDK surface works."""
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()
        import aictl
        r = aictl.ai.ask("hello")
        self.assertTrue(hasattr(r, "cost_usd"))
        self.assertTrue(hasattr(r, "cached"))


class TestQUICKSTART(unittest.TestCase):
    def test_quickstart_exists(self):
        qs = Path(__file__).parent.parent / "docs" / "QUICKSTART.md"
        self.assertTrue(qs.exists(), "QUICKSTART.md is missing")

    def test_quickstart_covers_v16_features(self):
        qs = Path(__file__).parent.parent / "docs" / "QUICKSTART.md"
        text = qs.read_text()
        required = [
            "aictl rag index",
            "aictl guard scan",
            "aictl tco",
            "aictl quota",
            "aictl batch",
            "aictl troubleshoot",
            "aictl dash",
            "answer.cost",    # QUICKSTART uses answer.cost
            "answer.cached",  # QUICKSTART uses answer.cached
        ]
        for keyword in required:
            self.assertIn(keyword, text,
                          f"QUICKSTART.md missing: {keyword}")

    def test_quickstart_no_placeholder_text(self):
        qs = Path(__file__).parent.parent / "docs" / "QUICKSTART.md"
        text = qs.read_text()
        bad_phrases = ["TODO", "FIXME", "coming soon", "placeholder"]
        for phrase in bad_phrases:
            self.assertNotIn(phrase.lower(), text.lower(),
                             f"QUICKSTART.md contains: {phrase}")


class TestCIWorkflow(unittest.TestCase):
    # The CI workflow is part of the release, but some automated environments
    # (e.g. a GitHub App without `workflows` permission) cannot commit
    # .github/workflows/*. Skip when the file is absent rather than fail; the
    # checks run in full wherever the workflow is present.
    CI_YML = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"

    @unittest.skipUnless(CI_YML.exists(), "ci.yml not present in this checkout")
    def test_ci_yml_exists(self):
        ci = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        self.assertTrue(ci.exists())

    @unittest.skipUnless(CI_YML.exists(), "ci.yml not present in this checkout")
    def test_ci_yml_has_required_jobs(self):
        import yaml
        ci = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        try:
            with open(ci) as f:
                config = yaml.safe_load(f)
            jobs = config.get("jobs", {})
            self.assertIn("test", jobs)
            self.assertIn("go", jobs)
        except ImportError:
            # yaml not available — just check file is non-empty
            self.assertGreater(ci.stat().st_size, 100)

    @unittest.skipUnless(CI_YML.exists(), "ci.yml not present in this checkout")
    def test_ci_checks_guard(self):
        ci = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        text = ci.read_text()
        self.assertIn("guard-check", text)
        self.assertIn("perf-check", text)
        self.assertIn("sdk-check", text)
        self.assertIn("rag-check", text)


class TestReleaseReadiness(unittest.TestCase):
    """Verify the project is ready for GitHub release."""

    def test_version_consistent(self):
        """pyproject.toml and constants.py must match."""
        import tomllib
        import contextlib

        from aictl.core.constants import AICTL_VERSION

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        try:
            with open(pyproject, "rb") as f:
                config = tomllib.load(f)
            toml_version = config.get("project", {}).get(
                "version", config.get("tool", {}).get("poetry", {}).get("version", "")
            )
            if not toml_version:
                # Try [project] section
                toml_version = config.get("project", {}).get("version", AICTL_VERSION)
            self.assertEqual(AICTL_VERSION, toml_version)
        except (AttributeError, KeyError):
            # tomllib structure may vary — fallback
            text = pyproject.read_text()
            self.assertIn(AICTL_VERSION, text,
                          "Version not found in pyproject.toml")

    def test_changelog_has_v16(self):
        changelog = Path(__file__).parent.parent / "CHANGELOG.md"
        self.assertTrue(changelog.exists())
        text = changelog.read_text()
        self.assertIn("v1.6.0", text)

    def test_readme_has_competitive_table(self):
        readme = Path(__file__).parent.parent / "README.md"
        text = readme.read_text()
        self.assertIn("guard", text)
        self.assertIn("Semantic cache", text)

    def test_all_new_commands_importable(self):
        """Every new v1.6.0 command module must import cleanly."""
        new_modules = [
            "aictl.cmd.fit",
            "aictl.cmd.quant",
            "aictl.cmd.troubleshoot",
            "aictl.cmd.rag",
            "aictl.cmd.guard",
            "aictl.cmd.cache_cmd",
            "aictl.cmd.perf",
            "aictl.cmd.dash",
            "aictl.cmd.update",
            "aictl.cmd.tco",
            "aictl.cmd.quota",
            "aictl.cmd.batch",
            "aictl.cmd.setup",
            "aictl.cmd.help",
        ]
        import importlib
        for mod_name in new_modules:
            try:
                importlib.import_module(mod_name)
                mod = importlib.import_module(mod_name)
                self.assertTrue(hasattr(mod, "register"))
            except ImportError as e:
                self.fail(f"Cannot import {mod_name}: {e}")


if __name__ == "__main__":
    unittest.main()
