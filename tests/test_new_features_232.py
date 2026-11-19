"""Pass 232: what the docs say about CI must match the CI that exists.

`.github/workflows/ci.yml` arrived in #57, but the docs had drifted both ways:
`REVIEW_v1.7.0.md` still called CI absent, and `SECURITY.md` claimed
`go mod verify` + `pip-audit` ran "in CI" when ci.yml never ran either. The
checks below are properties of the files, so they keep holding as ci.yml
grows: a tool may be advertised as running in CI exactly when ci.yml runs it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from aictl.core.constants import AICTL_VERSION

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
CI_YML = WORKFLOWS / "ci.yml"
PUBLIC_DOCS = ("README.md", "SECURITY.md", "CONTRIBUTING.md", "RELEASE.md")


def _ci_claim_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if re.search(r"\bin CI\b", ln)]


@unittest.skipUnless(CI_YML.exists(), "ci.yml not present in this checkout")
class TestCiClaimsMatchCi(unittest.TestCase):
    def setUp(self):
        self.ci = CI_YML.read_text()

    def test_tools_said_to_run_in_ci_are_in_ci_yml(self):
        for name in PUBLIC_DOCS:
            for line in _ci_claim_lines((ROOT / name).read_text()):
                for tool in re.findall(r"`([^`]+)`", line):
                    if "/" in tool:
                        continue
                    self.assertIn(tool, self.ci,
                                  f"{name} says `{tool}` runs in CI: {line.strip()}")

    def test_security_md_names_no_unrun_audit_as_ci(self):
        sec = (ROOT / "SECURITY.md").read_text()
        for tool in ("pip-audit", "go mod verify"):
            if tool not in self.ci:
                for line in _ci_claim_lines(sec):
                    self.assertNotIn(tool, line)

    def test_no_present_tense_claim_that_workflows_are_absent(self):
        for name in PUBLIC_DOCS:
            text = (ROOT / name).read_text()
            self.assertNotRegex(text, r"(?:has|have) no\s+`\.github/workflows/`",
                                f"{name} says the repo has no workflows")

    def test_readme_ci_badge_names_an_existing_workflow(self):
        readme = (ROOT / "README.md").read_text()
        m = re.search(r"actions/workflows/([\w.-]+\.ya?ml)/badge\.svg", readme)
        self.assertIsNotNone(m, "README has no CI badge")
        self.assertTrue((WORKFLOWS / m.group(1)).exists(), m.group(1))


class TestReadmeVersionSignboard(unittest.TestCase):
    def setUp(self):
        self.readme = (ROOT / "README.md").read_text()

    def test_install_version_matches_source(self):
        m = re.search(r"--version\s+#\s*aictl\s+(\S+)", self.readme)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), AICTL_VERSION)

    def test_readme_states_release_status_of_current_version(self):
        # The source version and the newest GitHub Release can differ; the
        # README must say which one a reader is getting.
        self.assertIn(f"v{AICTL_VERSION}", self.readme)
        self.assertIn("GitHub Release", self.readme)


if __name__ == "__main__":
    unittest.main()
