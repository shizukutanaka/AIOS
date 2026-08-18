"""Pass 213: the state directory was decided in fifteen places, so it split.

Found by walking the first-run journey with the state directory redirected, and
reading the one line of output nobody reads: `aictl init` printed
`State dir /root/.aios` while `AIOS_STATE_DIR` pointed somewhere else entirely.

Two writers, two directories, no warning:

    $ AIOS_STATE_DIR=/tmp/s aictl init && aictl chat hi
    /tmp/s/perf.jsonl        <- twelve modules honoured the variable
    ~/.aios/state.json       <- StateStore did not

`DEFAULT_STATE_DIR = Path.home() / ".aios"` was a module constant evaluated at
import, so `StateStore` — which owns `state.json`, `models.db`, the audit log
and the API keys — consulted no environment at all. Twelve modules read
`AIOS_STATE_DIR`, two read `AICTL_STATE_DIR`, and nothing reconciled them.

That also made a printed remedy false. `core/errors.py` answers a
PermissionError with "run with AIOS_STATE_DIR=/tmp/aios" — advice that did not
move the file whose permissions were the problem.

**The second split was underneath the first.** Fixing the environment variable
revealed that `--state-dir` had the same shape of bug: it moved `state.json`
and left `perf.jsonl` behind, because a dozen helpers resolve the directory
with no argparse namespace in hand and are reached from call sites with no
`args` to thread through. A global flag only moved what was handed it
explicitly. Publishing the flag into the environment in `__main__` fixes every
one of them at once, and carries the choice into subprocesses besides.

The tests below therefore assert an end-state, not an implementation: whatever
`aictl` writes, it all lands in one directory. The grep guard matters as much
as the behaviour — this bug existed because the resolution rule was *copied*
fifteen times, and a sixteenth copy would reintroduce it silently.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aictl.core.state import (
    STATE_DIR_ENV,
    STATE_DIR_ENV_ALIAS,
    StateStore,
    resolve_state_dir,
)

_ENV_NAMES = (STATE_DIR_ENV, STATE_DIR_ENV_ALIAS)


class _CleanEnv(unittest.TestCase):
    """Both names cleared, so each test states its own precondition."""

    def setUp(self) -> None:
        super().setUp()
        self._saved = {n: os.environ.get(n) for n in _ENV_NAMES}
        for n in _ENV_NAMES:
            os.environ.pop(n, None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for name, previous in self._saved.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


class TestPrecedence(_CleanEnv):
    def test_explicit_argument_wins_over_environment(self):
        # The flag is the user being specific right now; an inherited variable
        # outranking it would make `--state-dir` silently useless.
        os.environ[STATE_DIR_ENV] = "/tmp/from-env"
        self.assertEqual(resolve_state_dir("/tmp/explicit"), Path("/tmp/explicit"))

    def test_canonical_name_is_used(self):
        os.environ[STATE_DIR_ENV] = "/tmp/canonical"
        self.assertEqual(resolve_state_dir(), Path("/tmp/canonical"))

    def test_alias_is_honoured(self):
        # Two names were already in use, so neither could simply be deleted.
        os.environ[STATE_DIR_ENV_ALIAS] = "/tmp/alias"
        self.assertEqual(resolve_state_dir(), Path("/tmp/alias"))

    def test_canonical_beats_alias(self):
        os.environ[STATE_DIR_ENV] = "/tmp/canonical"
        os.environ[STATE_DIR_ENV_ALIAS] = "/tmp/alias"
        self.assertEqual(resolve_state_dir(), Path("/tmp/canonical"))

    def test_default_is_the_home_directory(self):
        self.assertEqual(resolve_state_dir(), Path.home() / ".aios")

    def test_empty_value_means_unset_not_cwd(self):
        # `AIOS_STATE_DIR= aictl ...` must not scatter state through whatever
        # tree the user happens to be standing in.
        for blank in ("", "   "):
            os.environ[STATE_DIR_ENV] = blank
            self.assertEqual(resolve_state_dir(), Path.home() / ".aios")

    def test_tilde_is_expanded(self):
        os.environ[STATE_DIR_ENV] = "~/custom-aios"
        self.assertEqual(resolve_state_dir(), Path.home() / "custom-aios")

    def test_path_object_is_accepted(self):
        self.assertEqual(resolve_state_dir(Path("/tmp/p")), Path("/tmp/p"))


class TestStateStoreFollowsTheEnvironment(_CleanEnv):
    """The half that was missing: StateStore consulted no environment at all."""

    def test_store_honours_the_variable(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ[STATE_DIR_ENV] = td
            self.assertEqual(StateStore().dir, Path(td))

    def test_store_honours_the_alias(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ[STATE_DIR_ENV_ALIAS] = td
            self.assertEqual(StateStore().dir, Path(td))

    def test_explicit_directory_still_wins(self):
        with tempfile.TemporaryDirectory() as env_dir, \
                tempfile.TemporaryDirectory() as explicit:
            os.environ[STATE_DIR_ENV] = env_dir
            self.assertEqual(StateStore(explicit).dir, Path(explicit))

    def test_state_json_lands_in_the_configured_directory(self):
        # The concrete symptom: state.json used to ignore the variable.
        with tempfile.TemporaryDirectory() as td:
            os.environ[STATE_DIR_ENV] = td
            store = StateStore()
            store.save_node(store.load_node())
            self.assertTrue((Path(td) / "state.json").exists())


class TestOneDirectoryEndToEnd(unittest.TestCase):
    """The property a user cares about: everything lands in one place.

    Run as a subprocess because the bug was about process-wide resolution, and
    an in-process test would share this interpreter's already-imported modules.
    """

    def _run(self, *argv, env_extra=None):
        env = dict(os.environ)
        for name in _ENV_NAMES:
            env.pop(name, None)
        env.update(env_extra or {})
        env["HOME"] = self._home
        return subprocess.run([sys.executable, "-m", "aictl", *argv],
                              capture_output=True, text=True, timeout=120,
                              cwd=str(Path(__file__).resolve().parent.parent),
                              env=env)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="aictl-e2e-")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self._home = str(root / "home")
        Path(self._home).mkdir()
        self.target = root / "target"

    def _home_state(self):
        return Path(self._home) / ".aios"

    def test_environment_variable_moves_everything(self):
        self._run("init", env_extra={STATE_DIR_ENV: str(self.target)})
        self.assertTrue((self.target / "state.json").exists(),
                        "state.json ignored the variable")
        self.assertFalse(self._home_state().exists(),
                         "something still wrote to ~/.aios")

    def test_alias_moves_everything_too(self):
        self._run("init", env_extra={STATE_DIR_ENV_ALIAS: str(self.target)})
        self.assertTrue((self.target / "state.json").exists())
        self.assertFalse(self._home_state().exists())

    def test_flag_moves_everything_including_the_perf_log(self):
        # The second split: --state-dir moved state.json and left perf.jsonl
        # in whatever directory the environment named.
        other = Path(self._tmp.name) / "env-side"
        self._run("--state-dir", str(self.target), "init",
                  env_extra={STATE_DIR_ENV: str(other)})
        self.assertTrue((self.target / "state.json").exists())
        self.assertFalse(other.exists() and any(other.iterdir()),
                         "a writer followed the environment past the flag")

    def test_nothing_leaks_to_home_when_redirected(self):
        self._run("init", env_extra={STATE_DIR_ENV: str(self.target)})
        self._run("status", env_extra={STATE_DIR_ENV: str(self.target)})
        self.assertFalse(self._home_state().exists())

    def test_default_still_uses_home(self):
        # The redirect must not have broken the ordinary case.
        self._run("init")
        self.assertTrue((self._home_state() / "state.json").exists())


class TestTheRuleIsNotCopiedAgain(unittest.TestCase):
    """This bug existed because one rule was written out fifteen times."""

    _ADHOC = re.compile(r"""environ(?:\.get)?\s*[\(\[]\s*["']AI(?:CTL|OS)_STATE_DIR""")

    def test_no_module_reimplements_the_resolution(self):
        offenders = []
        for path in Path("aictl").rglob("*.py"):
            if path.name in ("state.py", "partest.py"):
                continue          # the definition, and the subprocess launcher
            if self._ADHOC.search(path.read_text(errors="replace")):
                offenders.append(str(path))
        self.assertEqual(offenders, [],
                         "resolve_state_dir() exists so this rule lives in one "
                         "place; a copy here reintroduces the split")

    def test_default_constant_is_not_read_directly(self):
        # DEFAULT_STATE_DIR is evaluated at import, so a module using it
        # bypasses the environment exactly the way StateStore used to.
        #
        # Parsed rather than grepped: modules legitimately name the constant in
        # prose when explaining this history, and a substring check would fail
        # on the documentation instead of on a real reference.
        import ast

        offenders = []
        for path in Path("aictl").rglob("*.py"):
            if path.name == "state.py":
                continue
            tree = ast.parse(path.read_text(errors="replace"))
            if any(isinstance(n, ast.Name) and n.id == "DEFAULT_STATE_DIR"
                   for n in ast.walk(tree)):
                offenders.append(str(path))
        self.assertEqual(offenders, [])

    def test_resolution_is_actually_shared(self):
        # Guards against the reverse failure: deleting the copies but leaving
        # the callers pointed at something else.
        users = [p.name for p in Path("aictl").rglob("*.py")
                 if "resolve_state_dir" in p.read_text(errors="replace")]
        self.assertGreater(len(users), 8, f"only {len(users)} modules share it")


class TestTheSuiteIsHermetic(unittest.TestCase):
    """Running the tests must not touch the developer's real state directory.

    53 of 280 test files used to leave `models.db`, `rag.db`, `sem_cache.db`,
    `perf.jsonl`, the audit log or the daemon logs in `~/.aios`. Beyond
    mutating real data, it meant a test could pass on what a previous run left
    behind — a failure mode this codebase has already hit twice.
    """

    def test_state_dir_is_redirected_during_tests(self):
        self.assertTrue(any(os.environ.get(n) for n in _ENV_NAMES),
                        "tests/__init__.py should have redirected the state dir")

    def test_resolution_does_not_point_at_the_real_home(self):
        self.assertNotEqual(resolve_state_dir(), Path.home() / ".aios",
                            "the suite is writing into the real state directory")

    def test_redirect_respects_an_existing_value(self):
        # core/partest.py gives each parallel worker its own directory; the
        # suite-wide default must not overwrite it.
        source = Path("tests/__init__.py").read_text()
        self.assertIn("if not any(", source)

    def test_a_named_artifact_would_land_in_the_temp_dir(self):
        for artifact in ("sem_cache.db", "rag.db", "models.db", "perf.jsonl"):
            self.assertFalse((Path.home() / ".aios" / artifact).exists()
                             and resolve_state_dir() == Path.home() / ".aios",
                             f"{artifact} would be written to the real ~/.aios")


class TestThePrintedRemedyIsTrue(unittest.TestCase):
    """core/errors.py advises setting the variable; it must actually work."""

    def test_permission_error_advice_names_a_variable_that_works(self):
        source = Path("aictl/core/errors.py").read_text()
        match = re.search(r"(AI(?:CTL|OS)_STATE_DIR)", source)
        self.assertIsNotNone(match, "the remedy no longer names a variable")
        self.assertIn(match.group(1), _ENV_NAMES,
                      "advice names a variable nothing reads")

    def test_that_variable_moves_the_file_the_advice_is_about(self):
        # The advice exists to escape a PermissionError on the state files, so
        # it has to move state.json — which is precisely what it did not do.
        with tempfile.TemporaryDirectory() as td:
            saved = {n: os.environ.get(n) for n in _ENV_NAMES}
            try:
                for n in _ENV_NAMES:
                    os.environ.pop(n, None)
                os.environ[STATE_DIR_ENV] = td
                self.assertEqual(StateStore().dir, Path(td))
            finally:
                for name, previous in saved.items():
                    if previous is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = previous


if __name__ == "__main__":
    unittest.main()
