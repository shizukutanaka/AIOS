"""Pass 223: the re-inventory drifted from the code it was inventorying.

`docs/REVIEW_v1.7.0.md`'s 2026-09 addendum re-questioned every July claim
against today's code, which was the right method and found real answers. But
its own "next steps" list named **`go-port` version verification** as the
second-highest-priority remaining work — and that verification had shipped in
the same commit. Gate already reported `1.7.0 == pyproject.toml == go-port`.

The person who implemented it wrote the residual-work list minutes later
without re-reading the code. A document about documentation drift drifted, in
the section listing what to do next, about a fix in its own diff. Found only by
doing the same thing again: re-checking every claim rather than trusting the
most recent one.

`CLAUDE.md` had the same shape of error and had had it longer. It described
`aictl gate` as "Compile + import + version + tests + demo (~58s)" — five of
the twelve phases the gate actually runs, at roughly two-thirds of its measured
time. The omitted seven include the ones added most recently (Counts, Go port,
Docs, MCP, Security), which is exactly the direction such a list rots.

So the guard below is about *phases*, not prose: the documented phase list must
name every phase the gate really reports. Derived from `gate.run`'s source by
parsing the literals it appends, because grepping documentation for behaviour
is the habit that has now failed seven times in this session.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


def _gate_phase_names() -> set[str]:
    """Phase labels `gate.run` appends to its results list, read by parsing.

    Every phase is `results.append(("<Name>", ok, detail))`, so the labels are
    the first element of each appended tuple. Parsed rather than grepped: the
    module's comments legitimately name phases while explaining them.
    """
    from aictl.cmd import gate

    tree = ast.parse(Path(gate.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"):
            continue
        for arg in node.args:
            if (isinstance(arg, ast.Tuple) and arg.elts
                    and isinstance(arg.elts[0], ast.Constant)
                    and isinstance(arg.elts[0].value, str)):
                names.add(arg.elts[0].value)
    return names


class TestGatePhasesAreReal(unittest.TestCase):
    def test_the_parser_finds_the_known_phases(self):
        # Guards the guard: if the append pattern changes, this test must fail
        # rather than silently returning an empty set and passing everything.
        phases = _gate_phase_names()
        for expected in ("Compile", "Import", "Version", "Tests", "Docs"):
            self.assertIn(expected, phases)

    def test_more_than_a_handful_of_phases_exist(self):
        self.assertGreaterEqual(len(_gate_phase_names()), 10)


class TestClaudeMdDescribesEveryPhase(unittest.TestCase):
    """The documented list must not name a subset and imply completeness."""

    def _documented_line(self) -> str:
        text = Path("CLAUDE.md").read_text()
        start = text.index("aictl gate ")
        return text[start:start + 400].lower()

    def test_every_gate_phase_appears_in_the_description(self):
        described = self._documented_line()
        missing = [p for p in _gate_phase_names()
                   if p.lower().split()[0] not in described]
        self.assertEqual(missing, [],
                         f"CLAUDE.md's gate description omits {missing}")

    def test_the_phase_count_matches(self):
        # It said "Compile + import + version + tests + demo" — five of twelve.
        match = re.search(r"(\d+) phases", Path("CLAUDE.md").read_text())
        self.assertIsNotNone(match, "the description no longer states a count")
        self.assertEqual(int(match.group(1)), len(_gate_phase_names()))

    def test_no_absolute_runtime_is_promised(self):
        # "(~58s)" was measured at 88s. A wall-clock number in a doc rots on
        # every machine it is read from, so the claim is relative now.
        self.assertNotIn("~58s", Path("CLAUDE.md").read_text())


class TestTheAddendumMatchesTheCode(unittest.TestCase):
    """The specific drift this pass found, pinned so it cannot recur."""

    def setUp(self):
        self.review = Path("docs/REVIEW_v1.7.0.md").read_text()

    def test_it_no_longer_claims_gate_checks_only_two_sources(self):
        self.assertNotIn("`constants.py` と `pyproject.toml` の一致のみ検証",
                         self.review)

    def test_gate_really_does_check_the_third_source(self):
        # The claim's subject, verified against the code rather than the doc.
        source = Path("aictl/cmd/gate.py").read_text()
        self.assertIn("main.go", source)

    def test_the_next_steps_list_does_not_name_finished_work(self):
        next_steps = self.review[self.review.index("次に効果が大きい 3 件"):]
        self.assertNotIn("gate の Version フェーズに 3 つ目の情報源", next_steps)

    def test_the_drift_is_recorded_as_a_finding(self):
        # The document's own standard is to record what actually happened.
        self.assertIn("本追補自身が", self.review)


if __name__ == "__main__":
    unittest.main()
