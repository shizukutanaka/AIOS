"""Pass 215: the state-directory fix strands data, so it has to say so.

Pass 213 made `AIOS_STATE_DIR` move *all* of the state instead of some of it.
That is the fix. It is also, for anyone who had the variable set, a silent
change in what `aictl` can see: `state.json`, the model registry, the API keys,
the audit log and the metering ledger used to stay in `~/.aios` regardless, and
now they are simply not where aictl looks. Node config empty. No models. No
tenants.

Correct behaviour, indistinguishable from data loss. Every other finding this
session has been some version of undisclosed degradation, and shipping 213
without this would have been one more — authored deliberately, which is worse.

The migration command was itself wrong the first time, and only testing it
showed how. It used `cp -n`, which refuses to overwrite, and migrated nothing
at all in the sequence users actually follow: run a v1.7.0 command, notice
things missing, *then* look for instructions. That first command creates an
empty `state.json` and `models.db` in the new location, and `-n` then declines
to replace them. The fix is to overwrite that specific list deliberately —
safe precisely because none of those files was ever written to a configured
directory before v1.7.0, so anything there is the freshly-created empty.

The detection uses the same reasoning: a `state.json` of two bytes or fewer is
what a fresh run creates, so it does not count as "already migrated".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aictl.core.state import (
    LEGACY_HOME_ARTIFACTS,
    LEGACY_HOME_DIRS,
    split_state_warning,
)

_ENV = ("AIOS_STATE_DIR", "AICTL_STATE_DIR")


class TestWhenItSpeaks(unittest.TestCase):
    """Silence is the default; it warns only when data is genuinely stranded."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="aictl-warn-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        (self.home / ".aios").mkdir(parents=True)
        self.target = self.root / "target"
        self.target.mkdir()
        self._saved_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._saved_home

    def _legacy(self, content='{"node_id": "abc"}'):
        (self.home / ".aios" / "state.json").write_text(content)

    def test_warns_when_state_was_left_behind(self):
        self._legacy()
        self.assertIn("still in ~/.aios", split_state_warning(self.target))

    def test_silent_when_nothing_was_left_behind(self):
        self.assertEqual(split_state_warning(self.target), "")

    def test_silent_when_not_redirected(self):
        # The default layout cannot strand anything: it is where aictl looks.
        self._legacy()
        self.assertEqual(split_state_warning(self.home / ".aios"), "")

    def test_silent_once_migration_has_happened(self):
        self._legacy()
        (self.target / "state.json").write_text('{"node_id": "abc"}')
        self.assertEqual(split_state_warning(self.target), "")

    def test_a_freshly_created_empty_state_does_not_count_as_migrated(self):
        # `{}` is what a first v1.7.0 run writes. Treating that as "already
        # migrated" would suppress the warning for exactly the user who needs
        # it — the one who ran a command before reading anything.
        self._legacy()
        (self.target / "state.json").write_text("{}")
        self.assertIn("still in ~/.aios", split_state_warning(self.target))

    def test_never_raises_on_a_broken_path(self):
        self._legacy()
        self.assertIsInstance(split_state_warning(Path("/proc/1/nope/x")), str)


class TestWhatItSays(unittest.TestCase):
    """A warning that does not carry the fix just makes people anxious."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="aictl-warn2-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        (self.home / ".aios").mkdir(parents=True)
        (self.home / ".aios" / "state.json").write_text('{"node_id": "x"}')
        self.target = self.root / "target"
        self.target.mkdir()
        self._saved_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.__setitem__("HOME", self._saved_home)
                        if self._saved_home else os.environ.pop("HOME", None))
        self.message = split_state_warning(self.target)

    def test_names_the_directory_being_read(self):
        self.assertIn(str(self.target), self.message)

    def test_carries_a_runnable_command(self):
        self.assertIn("cp -a ", self.message)
        self.assertIn("cp -an ", self.message)

    def test_lists_every_stranded_artifact(self):
        for name in LEGACY_HOME_ARTIFACTS:
            self.assertIn(name, self.message, f"{name} missing from the command")

    def test_covers_the_stranded_directories(self):
        for name in LEGACY_HOME_DIRS:
            self.assertIn(name, self.message)

    def test_directories_are_merged_not_clobbered(self):
        # audit/, logs/ and plugins/ can legitimately hold both old and new
        # content, so their copy must not overwrite.
        tail = self.message[self.message.index("cp -an "):]
        for name in LEGACY_HOME_DIRS:
            self.assertIn(name, tail)

    def test_says_nothing_is_deleted(self):
        # Users will not run a command that might destroy the only copy.
        self.assertIn("Nothing is deleted", self.message)

    def test_points_at_the_fuller_explanation(self):
        self.assertIn("RELEASE.md", self.message)


class TestTheOverwriteIsDeliberate(unittest.TestCase):
    """`cp -n` here migrated nothing. The docs must not regress to it."""

    def test_release_notes_use_an_overwriting_copy_for_files(self):
        notes = Path("RELEASE.md").read_text()
        upgrade = notes[notes.index("## Upgrade notes"):]
        self.assertIn("cp -a ~/.aios/state.json", upgrade)
        self.assertNotIn("cp -n ~/.aios/state.json", upgrade,
                         "cp -n silently migrates nothing once a v1.7.0 "
                         "command has created the empty files")

    def test_release_notes_explain_why(self):
        notes = Path("RELEASE.md").read_text()
        self.assertIn("overwrites", notes)


class TestItDoesNotContaminateOutput(unittest.TestCase):
    """The warning must never break a script parsing --json."""

    def _run(self, *argv, env_extra=None):
        env = dict(os.environ)
        for name in _ENV:
            env.pop(name, None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, "-m", "aictl", *argv],
                              capture_output=True, text=True, timeout=120,
                              cwd=str(Path(__file__).resolve().parent.parent),
                              env=env)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="aictl-warn3-")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.home = root / "home"
        (self.home / ".aios").mkdir(parents=True)
        (self.home / ".aios" / "state.json").write_text('{"node_id": "x"}')
        self.target = root / "target"

    def test_json_output_stays_parseable(self):
        result = self._run("status", "--json",
                           env_extra={"HOME": str(self.home),
                                      "AIOS_STATE_DIR": str(self.target)})
        json.loads(result.stdout)          # raises if the warning leaked in

    def test_the_warning_reaches_stderr(self):
        result = self._run("status", "--json",
                           env_extra={"HOME": str(self.home),
                                      "AIOS_STATE_DIR": str(self.target)})
        self.assertIn("still in ~/.aios", result.stderr)

    def test_no_warning_in_the_ordinary_case(self):
        result = self._run("status", "--json", env_extra={"HOME": str(self.home)})
        self.assertNotIn("still in ~/.aios", result.stderr)

    def test_a_warning_never_changes_the_exit_code(self):
        # It is advice, not a failure. A CI job must not start failing because
        # someone's state needs migrating.
        warned = self._run("status", env_extra={"HOME": str(self.home),
                                                "AIOS_STATE_DIR": str(self.target)})
        quiet = self._run("status", env_extra={"HOME": str(self.home)})
        self.assertEqual(warned.returncode, quiet.returncode)


if __name__ == "__main__":
    unittest.main()
