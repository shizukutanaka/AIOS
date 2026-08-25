"""Pass 216: the command surface was hand-copied four times, and every copy lied.

aictl's real surface is whatever `build_parser()` registers — 80 commands.
Four places maintained their own written-out copy of that fact:

  * `gate`'s Docs phase checked a 10-name list frozen at v1.6.0. Directly
    below it, the CHANGELOG check was *derived* from VERSION, with a comment
    explaining exactly why hardcoded literals rot. The argument was applied to
    one check and not the two beside it. Better still: the phase built the
    full parser and computed `set(a.choices.keys())` — the true surface — and
    threw it away, unassigned.
  * `help` told users this was "the full 65-command surface" (80), and
    hand-maintained a category listing the gate's own check could never
    verify, because the names were written without the `aictl ` prefix it
    greps for.
  * `completion` hardcoded three separate lists — bash 38 names, zsh 17,
    fish 38 — so up to 63 of 80 commands had no tab completion. For
    completions this failure is invisible: a user cannot tab-complete a
    command they don't know exists, and never learns whose fault that was.
    The bash subcommand table knew five of `model`'s eight subcommands.

The fix is one derived source (`aictl/core/cli_surface.py`), consulted at
call time so plugin-registered commands ride along. The docs check gains the
reverse direction too — documentation naming a command that does not exist —
with a structural matching rule rather than a stopword list: `\\s` in the
reference regex crossed newlines and matched prose, and markdown prose
legitimately says "aictl does…", so only fenced/backticked contexts count.

Two checks guard the checkers themselves: a README scan that finds zero
references fails (a matcher that matches nothing is vacuously green forever),
and the gate's verdict is proven to be a *function of the derived set* by
patching the derivation and watching the verdict change — the one thing the
old dead-code version could not do.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aictl.core.cli_surface import (
    command_references,
    markdown_command_references,
    registered_commands,
    registered_subcommands,
)


class TestRegisteredCommandsHelper(unittest.TestCase):
    def test_matches_the_real_parser(self):
        from aictl.__main__ import build_parser

        parser = build_parser()
        expected = {name for a in parser._actions
                    if getattr(a, "choices", None) for name in a.choices}
        self.assertEqual(set(registered_commands()), expected)
        self.assertGreaterEqual(len(expected), 60)

    def test_every_command_has_help_text(self):
        # The gate's first derived check; pinned here so a new command
        # registered without help= fails close to the code that added it.
        empty = [n for n, h in registered_commands().items() if not h]
        self.assertEqual(empty, [])

    def test_late_registered_commands_are_present(self):
        names = set(registered_commands())
        for cmd in ("alert", "isolation", "help", "eval"):
            self.assertIn(cmd, names)

    def test_subcommands_are_discovered(self):
        subs = registered_subcommands()
        self.assertIn("pull", subs["model"])
        self.assertTrue(set(subs) <= set(registered_commands()))


class TestCommandReferenceScanner(unittest.TestCase):
    def test_same_line_reference_is_found(self):
        self.assertEqual(command_references("run aictl doctor today"), {"doctor"})

    def test_newline_does_not_join_a_reference(self):
        # The false-positive class that shaped the regex: `\s` crosses
        # newlines, turning "...aictl\nanswer..." into a ghost command.
        self.assertEqual(command_references("ends with aictl\nanswer next"), set())

    def test_markdown_prose_is_not_scanned(self):
        text = "What aictl does is simple. aictl exposes 19 tools."
        self.assertEqual(markdown_command_references(text), set())

    def test_fenced_code_is_scanned(self):
        text = "intro\n```bash\naictl doctor\n```\noutro"
        self.assertEqual(markdown_command_references(text), {"doctor"})

    def test_inline_backtick_is_scanned(self):
        self.assertEqual(markdown_command_references("run `aictl gate` often"),
                         {"gate"})


class TestGateDocsPhase(unittest.TestCase):
    """Drives gate._docs_issues directly."""

    def test_the_real_repo_is_clean(self):
        from aictl.cmd.gate import _docs_issues

        issues, detail = _docs_issues(Path("."))
        self.assertEqual(issues, [])
        self.assertIn(f"{len(registered_commands())} commands", detail)

    def _tmp_root(self, readme: str) -> Path:
        from aictl.__main__ import VERSION

        root = Path(tempfile.mkdtemp(prefix="aictl-docs-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, True))
        (root / "README.md").write_text(readme)
        (root / "CHANGELOG.md").write_text(f"## v{VERSION}\n")
        return root

    def test_a_ghost_command_in_readme_fails(self):
        from aictl.cmd.gate import _docs_issues

        root = self._tmp_root("```\naictl frobnicate\naictl doctor\n```\n")
        issues, _ = _docs_issues(root)
        self.assertTrue(any("frobnicate" in i for i in issues))

    def test_a_ghost_in_the_help_topics_fails(self):
        from aictl.cmd.gate import _docs_issues

        with patch("aictl.cmd.help.TOPICS",
                   {"x": "aictl doctor " * 30 + "and aictl imaginarycmd"}):
            issues, _ = _docs_issues(Path("."))
        self.assertTrue(any("imaginarycmd" in i for i in issues))

    def test_empty_topics_fail_on_the_coverage_floor(self):
        # The contract test_new_features_111b depends on: empty TOPICS must
        # fail the Docs phase, now via the coverage floor.
        from aictl.cmd.gate import _docs_issues

        with patch("aictl.cmd.help.TOPICS", {}):
            issues, _ = _docs_issues(Path("."))
        self.assertTrue(any("reference 0 commands" in i for i in issues))

    def test_missing_changelog_version_fails(self):
        from aictl.cmd.gate import _docs_issues

        root = self._tmp_root("```\naictl doctor\n```\n")
        (root / "CHANGELOG.md").write_text("## v0.0.1\n")
        issues, _ = _docs_issues(root)
        self.assertTrue(any("CHANGELOG" in i for i in issues))

    def test_a_readme_with_no_references_fails(self):
        # Guards the checker itself: a matcher that matches nothing would make
        # the ghost scan vacuously green forever.
        from aictl.cmd.gate import _docs_issues

        root = self._tmp_root("Pure prose, no commands at all.\n")
        issues, _ = _docs_issues(root)
        self.assertTrue(any("no aictl command references" in i for i in issues))

    def test_the_verdict_is_a_function_of_the_derived_set(self):
        # The behavioural proof the dead code is gone. The old phase computed
        # the registered set and discarded it, so patching the derivation
        # could not have changed its verdict. Now it must.
        from aictl.cmd import gate

        with patch("aictl.core.cli_surface.registered_commands",
                   return_value={}):
            issues, _ = gate._docs_issues(Path("."))
        self.assertTrue(issues, "an empty command surface must fail the docs")

    def test_the_frozen_list_is_gone_from_the_source(self):
        source = Path("aictl/cmd/gate.py").read_text()
        self.assertNotIn('"diff", "prompt", "route"', source,
                         "the v1.6.0 critical list is back")
        self.assertIn("cli_surface", source)


class TestCompletionsDerived(unittest.TestCase):
    def setUp(self):
        self.names = set(registered_commands())

    def test_bash_covers_the_full_surface(self):
        from aictl.cmd.completion import _bash_completion

        script = _bash_completion()
        line = next(l for l in script.splitlines()
                    if l.strip().startswith("commands="))
        listed = set(line.split('"')[1].split())
        self.assertEqual(self.names - listed, set())

    def test_bash_knows_subcommands_the_old_list_never_did(self):
        from aictl.cmd.completion import _bash_completion

        script = _bash_completion()
        self.assertIn("alert) COMPREPLY", script)      # absent from the old case
        self.assertIn("pull", script)                  # model's subcommands

    def test_zsh_covers_the_full_surface(self):
        from aictl.cmd.completion import _zsh_completion

        script = _zsh_completion()
        for name in self.names:
            self.assertIn(f"'{name}:", script, f"{name} missing from zsh")

    def test_zsh_escapes_apostrophes_in_help_text(self):
        # troubleshoot's help contains a bare apostrophe; unescaped it ends
        # the zsh string mid-entry and breaks every entry after it.
        from aictl.cmd.completion import _zsh_completion

        self.assertIn("'\\''", _zsh_completion())

    def test_fish_covers_the_full_surface(self):
        from aictl.cmd.completion import _fish_completion

        script = _fish_completion()
        for name in self.names:
            self.assertIn(f"-a {name} ", script + " ", f"{name} missing from fish")
        self.assertIn("__fish_seen_subcommand_from", script)

    def test_generators_work_with_no_arguments(self):
        # tests/test_cmd_coverage_1.py calls them bare; that must keep working.
        from aictl.cmd import completion

        for fn in (completion._bash_completion, completion._zsh_completion,
                   completion._fish_completion):
            self.assertTrue(fn().strip())


class TestHelpSurfaceHonesty(unittest.TestCase):
    def test_the_false_count_is_gone(self):
        # The property is "no false count claimed to users", checked in what
        # users see (TOPICS values), not the module source — a source-wide
        # substring check would fail on the comment explaining this history,
        # the same prose-vs-behaviour mistake made three times this session.
        import re

        from aictl.cmd.help import TOPICS

        real = len(registered_commands())
        for value in TOPICS.values():
            for claim in re.findall(r"(\d+)-command", value):
                self.assertEqual(int(claim), real,
                                 f"help claims a {claim}-command surface")
            self.assertNotIn("65-command", value)

    def test_advanced_lists_every_registered_command(self):
        import argparse
        import io
        from contextlib import redirect_stdout

        from aictl.cmd.help import run

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            run(argparse.Namespace(topic="advanced"))
        output = buffer.getvalue()
        commands = registered_commands()
        self.assertIn(f"All {len(commands)} commands:", output)
        for name in commands:
            self.assertIn(f"aictl {name}", output)

    def test_topics_remain_static_prose(self):
        # Mirrors the constraints test_ultra_dense/test_final_100 pin, close
        # to the code that changed: every topic stays >50 chars of prose
        # containing "aictl", so those suites cannot break silently.
        from aictl.cmd.help import TOPICS

        for key, value in TOPICS.items():
            self.assertGreater(len(value), 50, key)
            self.assertIn("aictl", value, key)


if __name__ == "__main__":
    unittest.main()
