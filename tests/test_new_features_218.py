"""Pass 218: the last unverified count, and the registration step people forget.

Three loose ends, two of them left by the two passes before this one.

**"30 REST API endpoints" was the last documented number nothing checked.**
Passes 216 and 217 derived the CLI commands, the Go commands and the MCP
tools; this one finishes the set. Routes are literal dicts inside the daemon's
`do_GET`/`do_POST` handlers, so they are read from the AST rather than by
starting a daemon and probing it — slow and side-effecting for a number used
to check one line of documentation.

`/metrics` is excluded deliberately, and the exclusion is the interesting part:
the handler has 31 routes, `/metrics` serves Prometheus text exposition rather
than JSON, and the REST API the docs count is the 30 under `/v1/`. A naive
route count earlier in this session concluded the documentation was wrong by
one. It was not — the fourth documented figure this session that looked wrong
and turned out to be right. Counting is not measuring.

**A command module can exist without being registered.** CLAUDE.md's own
workflow says "Register new commands in `__main__.py`", which is precisely the
kind of step a person forgets. Such a module imports cleanly, may have its own
tests, and the command simply does not exist for any user — invisible unless
something compares the directory against the parser. Two modules are named
`<command>_cmd.py` because their command name is a Python keyword or builtin
(`import`, `cache`), so the suffix is stripped before the lookup; that is a
convention, not a defect, and the check would be useless if it flagged them.

**Three places counted the same thing.** `info._count_commands()` walked the
parser itself and returned on the *first* subparsers action — a second would
have gone uncounted — so it now delegates to the one derived source.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from aictl.core.cli_surface import (
    registered_commands,
    rest_endpoint_count,
    unregistered_command_modules,
)
from aictl.core.docsync import check_counts, sync_counts


class TestRestEndpointCount(unittest.TestCase):
    def test_matches_the_documented_number(self):
        self.assertEqual(rest_endpoint_count(), 30)

    def test_excludes_the_prometheus_endpoint(self):
        # The handler has 31 routes; /metrics serves text exposition, not the
        # JSON REST API, so the documented 30 was right all along.
        import ast

        import aictl.daemon.aiosd as aiosd

        tree = ast.parse(Path(aiosd.__file__).read_text())
        all_routes = {
            k.value for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name.startswith("do_")
            for inner in ast.walk(n) if isinstance(inner, ast.Dict)
            for k in inner.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
            and k.value.startswith("/")
        }
        self.assertIn("/metrics", all_routes)
        self.assertEqual(len(all_routes) - 1, rest_endpoint_count())

    def test_counts_a_synthetic_handler(self):
        # Proves the reader works rather than that this repo happens to pass.
        with tempfile.TemporaryDirectory() as td:
            module = Path(td) / "fake.py"
            module.write_text(
                "class H:\n"
                "    def do_GET(self):\n"
                "        routes = {'/v1/a': 1, '/v1/b': 2, '/metrics': 3}\n"
                "        return routes\n")
            self.assertEqual(rest_endpoint_count(str(module)), 2)

    def test_unreadable_module_returns_zero(self):
        self.assertEqual(rest_endpoint_count("/nonexistent/aiosd.py"), 0)

    def test_unparseable_module_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / "broken.py"
            broken.write_text("def (:::\n")
            self.assertEqual(rest_endpoint_count(str(broken)), 0)


class TestUnregisteredCommandModules(unittest.TestCase):
    def test_this_repo_registers_everything(self):
        self.assertEqual(unregistered_command_modules(Path(".")), [])

    def test_the_keyword_named_modules_are_not_flagged(self):
        # cache_cmd.py and import_cmd.py register `cache` and `import`;
        # `import` is a Python keyword so the module cannot share the name.
        names = set(registered_commands())
        self.assertIn("import", names)
        self.assertIn("cache", names)
        self.assertTrue((Path("aictl/cmd") / "import_cmd.py").is_file())

    def test_an_unregistered_module_is_caught(self):
        # The forgotten step, simulated: a module in the directory that the
        # parser knows nothing about.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        cmd_dir = root / "aictl" / "cmd"
        cmd_dir.mkdir(parents=True)
        (cmd_dir / "__init__.py").write_text("")
        (cmd_dir / "neverregistered.py").write_text("def register(sub): pass\n")
        self.assertIn("neverregistered.py", unregistered_command_modules(root))

    def test_missing_directory_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(unregistered_command_modules(Path(td)), [])

    def test_gate_reports_orphans(self):
        import inspect

        from aictl.cmd import gate

        self.assertIn("unregistered_command_modules",
                      inspect.getsource(gate._docs_issues))


class TestRestClaimIsVerified(unittest.TestCase):
    def _project(self, claude: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix="aictl-rest-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        (root / "tests").mkdir()
        (root / "tests" / "test_a.py").write_text("")
        (root / "CLAUDE.md").write_text(claude)
        return root

    def test_a_wrong_rest_count_is_caught(self):
        root = self._project("aiosd(7 REST) governor proxy\n")
        self.assertTrue(any("REST endpoints" in str(p)
                            for p in check_counts(root)))

    def test_the_right_rest_count_passes(self):
        root = self._project("aiosd(30 REST) governor proxy\n")
        self.assertEqual([p for p in check_counts(root)
                          if "REST" in str(p)], [])

    def test_sync_repairs_it(self):
        root = self._project("aiosd(7 REST) governor proxy\n")
        sync_counts(root)
        self.assertIn("aiosd(30 REST)", (root / "CLAUDE.md").read_text())

    def test_this_repo_is_accurate(self):
        self.assertEqual([str(p) for p in check_counts(Path("."))], [])


class TestOneCounterNotThree(unittest.TestCase):
    def test_info_delegates_to_the_derived_source(self):
        from aictl.cmd.info import _count_commands

        self.assertEqual(_count_commands(), len(registered_commands()))

    def test_info_no_longer_walks_the_parser_itself(self):
        # It returned on the first subparsers action, so a second one would
        # have gone uncounted.
        import inspect

        from aictl.cmd import info

        source = inspect.getsource(info._count_commands)
        self.assertIn("registered_commands", source)
        self.assertNotIn("_actions", source)


if __name__ == "__main__":
    unittest.main()
