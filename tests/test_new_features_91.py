"""Pass 91 (loop, Socratic new perspective): command-suggestion hints must resolve.

New lens: the product is a connected workflow, not isolated commands. Commands
print actionable hints like "Try: aictl X" / "$ aictl Y" / "Materialize: aictl Z".
If a hint names a command that doesn't exist, the user hits a dead end. This was
real: setup.py suggested `aictl configure --engine cloud`, but `configure` is not
a command (it's `config`). This test scans user-facing hints across the package
and asserts every suggested top-level command is valid — a net for the whole class.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import unittest


def _valid_commands() -> set[str]:
    import aictl.__main__ as m
    parser = m.build_parser()
    cmds: set[str] = set()
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            cmds |= set(act.choices.keys())
    return cmds


# Hint prefixes that introduce a runnable command (avoids prose like "aictl can…").
_HINT = re.compile(
    r'(?:Try|Use|Next|Run|Run with|Materialize|Deploy with|Test now|Re-run|Install|Then|then):?\s*'
    r'aictl ([a-z][a-z0-9-]+)'
)
_SHELL = re.compile(r'\$ aictl ([a-z][a-z0-9-]+)')


class TestCommandHintsResolve(unittest.TestCase):

    def test_all_suggested_commands_exist(self):
        valid = _valid_commands()
        valid.add("gate")  # gate is a real entry point
        root = pathlib.Path(__file__).resolve().parent.parent / "aictl"
        broken: list[str] = []
        for f in root.rglob("*.py"):
            for i, line in enumerate(f.read_text().splitlines(), 1):
                for rx in (_HINT, _SHELL):
                    for mt in rx.finditer(line):
                        cmd = mt.group(1)
                        if cmd not in valid:
                            broken.append(f"{f.relative_to(root)}:{i}: 'aictl {cmd}' "
                                          f"is not a valid command")
        self.assertEqual(broken, [], "Broken command-suggestion hints:\n" + "\n".join(broken))

    def test_detects_a_known_bad_hint(self):
        # Guard the guard: the matcher must actually flag a bogus command.
        valid = _valid_commands()
        sample = "err('No models fit. Try: aictl configure --engine cloud')"
        found = [mt.group(1) for mt in _HINT.finditer(sample)]
        self.assertIn("configure", found)
        self.assertNotIn("configure", valid)


if __name__ == "__main__":
    unittest.main()
