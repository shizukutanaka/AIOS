"""Pass 228: every relative link in the docs was broken. All 25 of them.

Three findings, all from asking what nobody had checked.

**A documentation index with a 100% failure rate.** `docs/ai_os/README.md` is
the entry point to nine specifications, three OpenAPI files, six example
manifests and three CSVs. Every one of its 19 links was dead, and so were the
6 in its sibling documents — 25 of 25 relative links repo-wide. Two causes,
both mechanical: they used Windows `\\` separators, which no markdown renderer
resolves, and they were written repo-root-relative (`docs\\ai_os\\X.md`) inside
files that already live in `docs/ai_os/`. Every target existed the whole time.

**An example that contradicted the product's own policy.**
`examples/ollama.container` shipped `Image=docker.io/ollama/ollama:latest` with
`AutoUpdate=registry`. `constants.py` pins `OLLAMA_IMAGE = "ollama/ollama:0.20.0"`,
and its comment says why: a `:latest` tag in a Quadlet unit "is
unpullable-by-digest, silently changes under the operator, and cannot be
verified by the `aictl trust` subsystem this product ships". The example taught
the exact practice the constant exists to prevent, in the artifact type the
comment names.

**A spec for an API that does not exist.** `control-plane.openapi.yaml`
documents 13 paths; 11 have no implementation. It reads as API documentation
and is linked from the README as "Control Plane OpenAPI" beside a spec that is
real. It is a design document — now labelled as one, in the spec and in the
index, pointing readers at `aiosd-openapi.yaml` for the API that ships.

The guard is the general property: a relative link in a document must resolve.
That is cheap, total, and would have caught all 25.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
SKIP_PREFIXES = ("http://", "https://", "#", "mailto:")


def _markdown_files() -> list[Path]:
    return [p for p in sorted(Path(".").rglob("*.md"))
            if not any(x in p.parts for x in (".git", "node_modules"))]


def _relative_links(md: Path) -> list[str]:
    out = []
    for _, target in LINK.findall(md.read_text(errors="replace")):
        if target.startswith(SKIP_PREFIXES):
            continue
        clean = target.split("#")[0]
        if clean:
            out.append(clean)
    return out


class TestEveryRelativeLinkResolves(unittest.TestCase):
    def test_no_dead_links(self):
        dead = []
        for md in _markdown_files():
            for target in _relative_links(md):
                if not (md.parent / target).exists():
                    dead.append(f"{md} -> {target}")
        self.assertEqual(dead, [], f"dead documentation links: {dead}")

    def test_no_windows_separators(self):
        # The specific cause: `docs\\ai_os\\X.md` renders as literal text and
        # resolves nowhere, on every platform including Windows.
        offenders = [f"{md} -> {t}" for md in _markdown_files()
                     for t in _relative_links(md) if "\\" in t]
        self.assertEqual(offenders, [])

    def test_the_scanner_finds_links_at_all(self):
        # Guards the guard: zero links found would pass both tests forever.
        self.assertGreater(sum(len(_relative_links(md)) for md in _markdown_files()),
                           20)


class TestExamplesFollowTheProductsOwnPolicy(unittest.TestCase):
    """`constants.py` pins images and says why; the examples must agree."""

    def _quadlets(self) -> list[Path]:
        return sorted(Path("examples").glob("*.container"))

    def test_there_are_quadlet_examples_to_check(self):
        self.assertTrue(self._quadlets())

    def _image_lines(self, path: Path) -> list[str]:
        """Only `Image=` directives — not comments that discuss them.

        The first version of this test searched the raw file for ":latest" and
        failed on the comment explaining why :latest was removed. That is the
        ninth time in this session a substring check has caught prose instead
        of the construct it meant to check. The rule that keeps working: assert
        on the construct, never on the file's text.
        """
        return [ln for ln in path.read_text().splitlines()
                if ln.startswith("Image=")]

    def test_no_example_uses_a_latest_tag(self):
        offenders = [f"{p}: {ln}" for p in self._quadlets()
                     for ln in self._image_lines(p) if ln.endswith(":latest")]
        self.assertEqual(offenders, [],
                         "a :latest tag cannot be verified by aictl trust")

    def test_every_image_carries_an_explicit_tag(self):
        for path in self._quadlets():
            for line in self._image_lines(path):
                self.assertIn(":", line.split("=", 1)[1],
                              f"{path} pulls an untagged image")

    def test_the_ollama_example_matches_the_pinned_constant(self):
        from aictl.core.constants import OLLAMA_IMAGE

        text = Path("examples/ollama.container").read_text()
        self.assertIn(OLLAMA_IMAGE, text)


class TestSpecsSayWhetherTheyAreImplemented(unittest.TestCase):
    """A design document beside a real one must not read like a real one."""

    def _served(self) -> set[str]:
        import aictl.daemon.aiosd as aiosd

        tree = ast.parse(Path(aiosd.__file__).read_text(encoding="utf-8"))
        return {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value.startswith("/")}

    def _paths(self, name: str) -> set[str]:
        return set(re.findall(
            r"^  (/[^\s:]*):",
            Path(f"docs/ai_os/{name}.openapi.yaml").read_text(encoding="utf-8"),
            re.M))

    def test_the_runtime_broker_spec_is_fully_implemented(self):
        # It was, and still should be — this catches drift in the other spec
        # that turned out to be accurate.
        self.assertEqual(self._paths("runtime-broker") - self._served(), set())

    def test_an_unimplemented_spec_says_so(self):
        text = Path("docs/ai_os/control-plane.openapi.yaml").read_text()
        unimplemented = self._paths("control-plane") - self._served()
        if unimplemented:
            self.assertIn("NOT IMPLEMENTED", text,
                          "a spec whose paths have no code must say so")

    def test_the_index_distinguishes_them(self):
        readme = Path("docs/ai_os/README.md").read_text()
        self.assertIn("actually ships", readme)
        self.assertIn("not implemented", readme.lower())

    def test_readers_are_pointed_at_the_real_spec(self):
        self.assertIn("aiosd-openapi.yaml",
                      Path("docs/ai_os/control-plane.openapi.yaml").read_text())


if __name__ == "__main__":
    unittest.main()
