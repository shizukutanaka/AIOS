"""Pass 227: the API spec described 26 of the daemon's 30 endpoints.

`CLAUDE.md` advertises an OpenAPI spec under `docs/`; the daemon serves 30
`/v1/` routes. Nothing had ever compared the two, so the spec had drifted three
separate ways at once:

  * **Five routes were undocumented** — `/v1/scheduler`, `/v1/models/register`,
    `/v1/recipes/run`, `/v1/stacks/apply`, `/v1/stacks/down`. Four of the five
    are POSTs: the state-changing half of the API, which is exactly the part a
    consumer most needs a spec for. Reading it, you would not know they exist.
  * **Its version said 1.5.0** — two releases behind — because nothing compared
    it to anything. `gate`'s Version phase already reconciled `constants.py`,
    `pyproject.toml` and the Go port; the spec is a fourth place the version
    exists as a value, so it now joins them.
  * **Its own summary was wrong about itself.** It claimed "22 GET endpoints +
    4 POST endpoints"; it documented 21 GET + 4 POST, against a real 22 + 8.
    A document can be stale about the code and about its own contents at once.

The guard below is the parity check, derived on both sides: routes come from
the handler's AST (the same source `rest_endpoint_count()` uses) and paths from
the spec, so neither number is written down anywhere a person has to maintain.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SPEC = Path("docs/ai_os/aiosd-openapi.yaml")


def _served_routes() -> set[str]:
    """Every `/v1/` path literal in the daemon handler, read by parsing.

    Parsed rather than grepped for the same reason `cli_surface` parses: a path
    named in a docstring or comment is not a route.
    """
    import aictl.daemon.aiosd as aiosd

    tree = ast.parse(Path(aiosd.__file__).read_text(encoding="utf-8"))
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.startswith("/v1/")}


def _documented_paths() -> set[str]:
    """Top-level keys under `paths:` in the spec.

    A two-space-indented key starting with `/`. Extracted with a regex rather
    than a YAML parser because this project has no external dependencies, and
    the shape being checked is exactly this one line form.
    """
    return set(re.findall(r"^  (/[^\s:]*):", SPEC.read_text(encoding="utf-8"), re.M))


class TestSpecCoversTheApi(unittest.TestCase):
    def test_every_served_route_is_documented(self):
        missing = sorted(_served_routes() - _documented_paths())
        self.assertEqual(missing, [],
                         f"served but absent from the OpenAPI spec: {missing}")

    def test_the_spec_documents_no_route_that_does_not_exist(self):
        # The other direction: a documented endpoint that 404s is worse than
        # an undocumented one that works.
        extra = sorted(_documented_paths() - _served_routes() - {"/metrics"})
        self.assertEqual(extra, [],
                         f"documented but not served: {extra}")

    def test_metrics_is_documented_and_served(self):
        # Excluded from the "30 REST endpoints" count on purpose — it serves
        # Prometheus text, not JSON — but it is a real endpoint and belongs in
        # the spec.
        import aictl.daemon.aiosd as aiosd

        self.assertIn("/metrics", _documented_paths())
        self.assertIn('"/metrics"',
                      Path(aiosd.__file__).read_text(encoding="utf-8"))

    def test_both_sides_are_actually_populated(self):
        # Guards the guard: two empty sets are equal, and would pass forever.
        self.assertGreater(len(_served_routes()), 20)
        self.assertGreater(len(_documented_paths()), 20)


class TestSpecSelfDescriptionIsTrue(unittest.TestCase):
    """It claimed a shape that matched neither the API nor itself."""

    def setUp(self):
        self.text = SPEC.read_text(encoding="utf-8")

    def _counts(self) -> tuple[int, int]:
        """(GET, POST) route counts from the handler."""
        import aictl.daemon.aiosd as aiosd

        tree = ast.parse(Path(aiosd.__file__).read_text(encoding="utf-8"))
        found = {}
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name in ("do_GET", "do_POST"):
                found[fn.name] = len({
                    n.value for n in ast.walk(fn)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and n.value.startswith("/v1/")})
        return found.get("do_GET", 0), found.get("do_POST", 0)

    def test_the_described_counts_match_the_handler(self):
        gets, posts = self._counts()
        match = re.search(r"(\d+) GET endpoints \+ (\d+) POST endpoints", self.text)
        self.assertIsNotNone(match, "the spec no longer states its shape")
        self.assertEqual((int(match.group(1)), int(match.group(2))), (gets, posts))

    def test_the_version_matches_the_project(self):
        from aictl.__main__ import VERSION

        match = re.search(r'^\s*version:\s*"([^"]+)"', self.text, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), VERSION)


class TestGateChecksTheSpecVersion(unittest.TestCase):
    """A fourth place the version exists as a value, so a fourth source."""

    def test_the_version_phase_reads_the_spec(self):
        import inspect

        from aictl.cmd import gate

        self.assertIn("aiosd-openapi.yaml", inspect.getsource(gate.run))

    def test_the_spec_version_reaches_the_comparison(self):
        import inspect

        from aictl.cmd import gate

        text = inspect.getsource(gate.run)
        marker = text.index("aiosd-openapi.yaml")
        # The extracted value has to reach `sources`, or it is read and dropped
        # exactly the way the Docs phase once dropped the command set.
        self.assertIn("sources[\"openapi\"]", text[marker:marker + 700])


if __name__ == "__main__":
    unittest.main()
