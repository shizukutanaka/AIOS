"""Pass 219: quantization advice for a use case nobody measured, and a
release target that announced work it never did.

Two findings, both the same disease this session keeps turning up — a
confident-looking output with nothing behind it.

**Proxied quality numbers.** `aictl quant recommend --use-case embedding`
accepted `embedding` (quantizing an embedding model is a real thing to want
advice about) and then scored it with `d.get("q_embedding", d["q_chat"])`.
There is no `q_embedding` anywhere in `QUANT_DATA`, so every number printed
under the heading "Use case: embedding" was chat quality wearing a different
label. The fix is not to remove the choice — it is to say so. `measured_use_cases()`
derives the answer from the data (`q_*` keys) rather than hardcoding a list
that would drift the moment someone adds `q_embedding` for real.

**A release target that lied.** `make release` was documented as "Tag and push
(triggers CI → PyPI → Docker)" and ended by printing `✓ v1.7.0 released.` The
repository has no `.github/workflows/` directory at all, so the tag triggered
nothing: no CI, no PyPI, no Docker, and — the reason this matters — no GitHub
Release object, which is exactly why the Releases tab sat at v1.6.0 while
`constants.py` and `CHANGELOG.md` both said 1.7.0. A maintainer ran it, read a
success line, and waited for a package that was never coming.

The repair is the one this session has reached for repeatedly: not to build a
fake pipeline so the comment becomes true, but to delete the false claim and
make the command state what actually happened. `release` now creates the
GitHub Release itself when `gh` is available, and when it is not it prints the
remaining step rather than a checkmark. It also refuses to tag a dirty tree —
the previous version would happily have tagged a release that omitted the
uncommitted work sitting beside it.
"""

from __future__ import annotations

import argparse
import io
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from aictl.cmd.quant import QUANT_DATA, measured_use_cases


class TestMeasuredUseCasesIsDerived(unittest.TestCase):
    def test_matches_the_data_not_a_hardcoded_list(self):
        expected = set()
        for entry in QUANT_DATA.values():
            expected.update(k[2:] for k in entry if k.startswith("q_"))
        self.assertEqual(set(measured_use_cases()), expected)

    def test_chat_is_measured(self):
        # The fallback everything proxies to; if this were missing the
        # disclaimer would fire for every use case.
        self.assertIn("chat", measured_use_cases())

    def test_embedding_is_not_measured(self):
        # The concrete gap: an accepted --use-case with no quality data.
        self.assertNotIn("embedding", measured_use_cases())

    def test_result_is_sorted_and_deduplicated(self):
        cases = measured_use_cases()
        self.assertEqual(cases, sorted(set(cases)))


class TestTheChoiceIsWiderThanTheData(unittest.TestCase):
    """Deliberately: the fix is disclosure, not removing the option."""

    def _choices(self) -> set[str]:
        source = Path("aictl/cmd/quant.py").read_text()
        match = re.search(r'"--use-case".*?choices=\[([^\]]*)\]', source, re.S)
        self.assertIsNotNone(match, "--use-case choices not found")
        return set(re.findall(r'"([a-z]+)"', match.group(1)))

    def test_every_measured_case_is_offered(self):
        self.assertTrue(set(measured_use_cases()) <= self._choices())

    def test_at_least_one_offered_case_is_proxied(self):
        # If this ever becomes empty the disclaimer is dead code and should go.
        self.assertTrue(self._choices() - set(measured_use_cases()))


class TestCliDisclosesTheProxy(unittest.TestCase):
    def _recommend(self, use_case: str) -> str:
        from aictl.cmd.quant import run_recommend

        namespace = argparse.Namespace(model="llama3.1:8b", gpu="H100",
                                       use_case=use_case, json=False)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            run_recommend(namespace)
        return buffer.getvalue()

    def test_measured_use_case_says_nothing_extra(self):
        output = self._recommend("chat")
        self.assertIn("Use case: chat", output)
        self.assertNotIn("proxy", output)

    def test_unmeasured_use_case_names_the_gap(self):
        output = self._recommend("embedding")
        self.assertIn("no embedding-specific quality data", output)
        self.assertIn("proxy", output)

    def test_the_disclaimer_sits_with_the_number_it_qualifies(self):
        # On the "Use case:" line rather than a footnote, because a caveat
        # printed far from the figure it qualifies is one nobody reads.
        line = next(l for l in self._recommend("embedding").splitlines()
                    if "Use case:" in l)
        self.assertIn("proxy", line)


class TestMcpToolDisclosesTheSameGap(unittest.TestCase):
    """The MCP client sees numbers too, and had the identical silent fallback."""

    def _quant(self, use_case: str) -> str:
        from aictl.mcp_server import _tool_quant

        result = _tool_quant({"model": "llama3.1:8b", "use_case": use_case})
        return str(result)

    def test_unmeasured_use_case_is_disclosed(self):
        self.assertIn("no embedding-specific quality data",
                      self._quant("embedding"))

    def test_measured_use_case_is_not(self):
        self.assertNotIn("proxy", self._quant("chat"))

    def test_both_surfaces_use_one_source(self):
        # Two copies of "which use cases are measured" would drift apart the
        # first time someone added q_embedding.
        source = Path("aictl/mcp_server.py").read_text()
        self.assertIn("measured_use_cases", source)


class TestReleaseTargetTellsTheTruth(unittest.TestCase):
    """The Makefile must not advertise machinery the repo does not have."""

    def setUp(self):
        self.makefile = Path("Makefile").read_text()
        self.has_workflows = any(Path(".github/workflows").glob("*.yml")) \
            if Path(".github/workflows").is_dir() else False

    def test_no_claim_of_github_actions_without_workflows(self):
        # A property check, not a substring ban: this starts passing honestly
        # the day someone actually adds a workflow.
        if self.has_workflows:
            self.skipTest("workflows exist; the claim would be true")
        for phrase in ("runs in GitHub Actions", "triggers CI"):
            self.assertNotIn(phrase, self.makefile,
                             f"Makefile claims {phrase!r} with no workflows")

    def test_release_does_not_announce_an_untriggered_pipeline(self):
        if self.has_workflows:
            self.skipTest("workflows exist; the claim would be true")
        self.assertNotIn("PyPI", self.makefile,
                         "nothing in this repo publishes to PyPI")

    def test_release_refuses_a_dirty_tree(self):
        # Tagging with uncommitted changes produces a tag that omits them.
        self.assertIn("git diff --quiet", self.makefile)

    def test_release_creates_the_github_release_when_it_can(self):
        self.assertIn("gh release create", self.makefile)

    def test_release_notes_come_from_the_maintained_file(self):
        self.assertIn("RELEASE.md", self.makefile)
        self.assertTrue(Path("RELEASE.md").read_text().strip())

    def test_release_check_verifies_the_changelog(self):
        # `make release` must not tag a version the CHANGELOG never mentions.
        self.assertIn("CHANGELOG.md", self.makefile)


if __name__ == "__main__":
    unittest.main()
