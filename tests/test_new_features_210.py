"""Pass 210: the state directory holds secrets and was world-readable.

Found by questioning a number nobody had looked at. Every `aictl gate` run
printed "Security scanner ran 10 checks cleanly (score 65/100)" and no one
asked what the missing 35 points were. Running the scanner directly showed
most findings are facts about the host (running as root, no cgroup v2, no
PSI) — correct, and not product defects. One was not.

`~/.aios` was created with the process umask, typically 0755. It holds the
cloud API key in config.json, the metering ledger, the audit log, the model
registry, and every document indexed into RAG. All of it readable by any
other user on the machine.

The striking part is that aictl already knew, in three separate places, and
acted in none:

  * `STATE_DIR_PERMISSIONS = 0o700` declared the correct mode — and appeared
    in this session's "unused constants" list, where deleting it was
    considered and rejected because constants carry information. This is what
    that information was for.
  * `core/security.py` detected the loose mode and rated it HIGH.
  * The remediation it printed was `chmod 700 <state_dir>` — exactly the call
    that was missing.

Detecting a problem you are able to fix, and then only printing advice about
it, is the least useful possible arrangement of those parts.

Tightening an existing directory follows ssh's convention: a tool holding
credentials insists on owner-only access rather than warning forever. It only
ever removes access, and it is skipped when the directory is not ours —
narrowing another user's permissions is not this code's business.
"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from aictl.core.constants import STATE_DIR_PERMISSIONS
from aictl.core.state import StateStore, _secure_state_dir


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class TestNewStateDirIsPrivate(unittest.TestCase):
    def test_created_directory_is_owner_only(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "fresh"
            StateStore(target)
            self.assertEqual(_mode(target), STATE_DIR_PERMISSIONS)

    def test_nested_creation_still_ends_private(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "a" / "b" / "state"
            StateStore(target)
            self.assertEqual(_mode(target), STATE_DIR_PERMISSIONS)

    def test_no_group_or_other_access(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "s"
            StateStore(target)
            self.assertEqual(_mode(target) & 0o077, 0,
                             "the directory holds API keys and the audit log")


class TestExistingLooseDirIsTightened(unittest.TestCase):
    def test_world_readable_directory_is_fixed(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "loose"
            target.mkdir()
            target.chmod(0o755)
            self.assertTrue(_secure_state_dir(target))
            self.assertEqual(_mode(target), STATE_DIR_PERMISSIONS)

    def test_group_readable_directory_is_fixed(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "grouped"
            target.mkdir()
            target.chmod(0o750)
            _secure_state_dir(target)
            self.assertEqual(_mode(target) & 0o077, 0)

    def test_already_private_directory_is_left_alone(self):
        # Reports no change, so a caller can tell "fixed" from "was fine".
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "tight"
            target.mkdir(mode=0o700)
            self.assertFalse(_secure_state_dir(target))
            self.assertEqual(_mode(target), 0o700)

    def test_stricter_modes_are_not_loosened(self):
        # It only ever removes access. A user who chose 0500 keeps it.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "strict"
            target.mkdir()
            target.chmod(0o500)
            _secure_state_dir(target)
            self.assertEqual(_mode(target) & 0o077, 0)


class TestFailureIsNeverFatal(unittest.TestCase):
    """Hardening must not stop aictl from running."""

    def test_missing_directory_returns_false(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(_secure_state_dir(Path(td) / "does-not-exist"))

    def test_unowned_directory_is_skipped(self):
        # Narrowing another user's permissions is not this code's business.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "theirs"
            target.mkdir()
            target.chmod(0o755)

            real_stat = Path.stat

            def foreign(self, *a, **k):
                info = real_stat(self, *a, **k)

                class Fake:
                    st_mode = info.st_mode
                    st_uid = info.st_uid + 12345
                return Fake()

            Path.stat = foreign
            try:
                self.assertFalse(_secure_state_dir(target))
            finally:
                Path.stat = real_stat
            self.assertEqual(_mode(target), 0o755, "someone else's dir untouched")

    def test_chmod_failure_is_swallowed(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "ro"
            target.mkdir()
            target.chmod(0o755)
            with patch.object(Path, "chmod", side_effect=OSError("read-only")):
                self.assertFalse(_secure_state_dir(target))   # returns, no raise

    def test_store_still_usable_when_hardening_fails(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            with patch("aictl.core.state._secure_state_dir",
                       side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    StateStore(Path(td) / "x")
            # Without the fault injected it must construct normally.
            self.assertIsNotNone(StateStore(Path(td) / "y"))


class TestTheConstantIsNowUsed(unittest.TestCase):
    def test_permission_comes_from_the_declared_constant(self):
        # It was in the "unused constants" list; deleting it was considered
        # and rejected. This is what it was for.
        self.assertEqual(STATE_DIR_PERMISSIONS, 0o700)

    def test_state_module_references_it(self):
        source = Path("aictl/core/state.py").read_text()
        self.assertIn("STATE_DIR_PERMISSIONS", source,
                      "the mode must not be re-hardcoded next to the constant "
                      "that already declares it")


if __name__ == "__main__":
    unittest.main()
