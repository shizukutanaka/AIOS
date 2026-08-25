"""Pass 220: the installer verified one Python and then installed another.

`scripts/install.sh` is the first thing a user runs — `curl … | bash` is the
documented entry point in the README — and nothing had ever tested it.

It searched `python3.13 → python3.12 → python3.11 → python3` for an interpreter
new enough to run aictl, printed the one it found, and then wrote this wrapper:

    sudo tee "$BIN_LINK" > /dev/null << 'EOF'
    #!/bin/bash
    export PYTHONPATH=/opt/aios:${PYTHONPATH:-}
    exec python3 -m aictl "$@"
    EOF

The heredoc is quoted, so `$PYTHON` never interpolates: the wrapper always ran
`python3`. On a machine where `python3` is 3.9 and `python3.11` is installed —
precisely the machine the search loop exists for — the installer printed
"Python: python3.11", reported "Installation verified", and left behind an
`aictl` that failed on first use. The check was computed and discarded, which
is the same defect this session found in the gate's Docs phase, in the first
thing a user touches rather than in a maintainer tool.

Three more, found while confirming that one:

  * **The update path could not work.** `cd "$INSTALL_DIR" && git pull` ran
    unprivileged against a clone created by `sudo git clone`, so re-running the
    installer to update failed — and `set -euo pipefail` turned that into an
    abort. It also tested `-d "$INSTALL_DIR"`, so a directory that existed but
    was not a git checkout took the pull branch and died.
  * **Verification tested the wrong file.** `if aictl --help` resolves through
    PATH, so it checked whatever `aictl` PATH found first — an older install,
    or nothing at all when `/usr/local/bin` is not on PATH — rather than the
    file just written.
  * **The version comparison treated the two numbers as independent.**
    `[ "$major" -ge 3 ] && [ "$minor" -ge 11 ]` rejects a hypothetical 4.0,
    because 0 is not ≥ 11.

The script now exposes `detect_python` and `write_wrapper` and installs only
when not sourced, so these are testable at all — the reason none of it was
caught is that a 100-line shell script with no seams cannot be tested without
running a real install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

INSTALLER = Path("scripts/install.sh").resolve()


def _bash(snippet: str, path_prefix: str | None = None) -> subprocess.CompletedProcess:
    """Run a bash snippet with install.sh sourced as a library."""
    env = dict(os.environ, AIOS_INSTALL_LIB="1")
    if path_prefix:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
    script = f'source "{INSTALLER}"\nSUDO=""\n{textwrap.dedent(snippet)}'
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=60, env=env)


class _FakePythons(unittest.TestCase):
    """A sandbox PATH with interpreters of chosen versions."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aictl-inst-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()

    def _stub(self, name: str, version: str, works: bool = True) -> None:
        body = (f'#!/bin/bash\n'
                f'[ "$1" = "-c" ] && {{ echo "{version}"; exit 0; }}\n'
                f'[ "$1" = "--version" ] && {{ echo "Python {version}.0"; exit 0; }}\n'
                f'exit {0 if works else 1}\n')
        target = self.bin / name
        target.write_text(body)
        target.chmod(0o755)

    def _shadow_all(self, version: str = "3.9") -> None:
        for name in ("python3", "python3.11", "python3.12", "python3.13"):
            self._stub(name, version)


class TestDetectPython(_FakePythons):
    def test_prefers_the_newest_available(self):
        self._shadow_all("3.9")
        self._stub("python3.13", "3.13")
        self._stub("python3.11", "3.11")
        result = _bash("detect_python", str(self.bin))
        self.assertTrue(result.stdout.strip().endswith("python3.13"),
                        result.stdout)

    def test_skips_interpreters_that_are_too_old(self):
        # The case the whole loop exists for: python3 is old, python3.11 is not.
        self._shadow_all("3.9")
        self._stub("python3.11", "3.11")
        self.assertTrue(_bash("detect_python", str(self.bin))
                        .stdout.strip().endswith("python3.11"))

    def test_fails_when_nothing_is_new_enough(self):
        self._shadow_all("3.9")
        result = _bash("detect_python || echo NONE", str(self.bin))
        self.assertIn("NONE", result.stdout)

    def test_a_future_major_version_is_accepted(self):
        # `[ "$major" -ge 3 ] && [ "$minor" -ge 11 ]` rejected 4.0 because
        # 0 is not >= 11. The comparison must treat the version as a pair.
        self._shadow_all("3.9")
        self._stub("python3.13", "4.0")
        self.assertTrue(_bash("detect_python", str(self.bin)).stdout.strip())

    def test_three_ten_is_rejected_and_three_eleven_accepted(self):
        # The documented floor is 3.11. Checked as behaviour across the
        # boundary rather than by reading the comparison out of the source.
        self._shadow_all("3.10")
        self.assertIn("NONE", _bash("detect_python || echo NONE",
                                    str(self.bin)).stdout)
        self._stub("python3.11", "3.11")
        self.assertTrue(_bash("detect_python", str(self.bin)).stdout.strip())

    def test_returns_an_absolute_path(self):
        # The wrapper embeds this, and PATH at run time is not PATH at
        # install time.
        self._shadow_all("3.9")
        self._stub("python3.11", "3.11")
        found = _bash("detect_python", str(self.bin)).stdout.strip()
        self.assertTrue(found.startswith("/"), found)


class TestWrapperPinsTheVerifiedInterpreter(_FakePythons):
    def _wrapper(self) -> str:
        target = Path(self.tmp) / "aictl"
        _bash(f'write_wrapper "/usr/bin/python3.11" "{target}" "/opt/aios"')
        return target.read_text()

    def test_wrapper_uses_the_interpreter_it_was_given(self):
        self.assertIn("exec /usr/bin/python3.11 -m aictl", self._wrapper())

    def test_wrapper_does_not_fall_back_to_bare_python3(self):
        # The original defect, stated as the property it violated.
        self.assertNotIn("exec python3 -m aictl", self._wrapper())

    def test_wrapper_keeps_the_install_dir_on_pythonpath(self):
        self.assertIn("export PYTHONPATH=/opt/aios:${PYTHONPATH:-}",
                      self._wrapper())

    def test_wrapper_forwards_arguments(self):
        self.assertIn('"$@"', self._wrapper())

    def test_wrapper_is_executable(self):
        target = Path(self.tmp) / "aictl"
        _bash(f'write_wrapper "/usr/bin/python3" "{target}" "/opt/aios"')
        self.assertTrue(os.access(target, os.X_OK))

    def test_generated_wrapper_actually_runs_aictl(self):
        # End to end: the wrapper the installer writes must start this
        # checkout's aictl, not merely look plausible.
        import sys

        target = Path(self.tmp) / "aictl"
        repo = Path(__file__).resolve().parent.parent
        _bash(f'write_wrapper "{sys.executable}" "{target}" "{repo}"')
        result = subprocess.run([str(target), "--version"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("aictl", result.stdout)


class TestTheRemainingThreeDefects(unittest.TestCase):
    """Properties of the script, each one a bug that shipped."""

    def setUp(self):
        self.source = INSTALLER.read_text()

    def test_update_path_is_privileged_like_the_clone(self):
        # `git pull` ran unprivileged against a root-owned clone.
        self.assertIn('$SUDO git -C "$INSTALL_DIR" pull', self.source)

    def test_update_path_requires_a_real_checkout(self):
        # `-d "$INSTALL_DIR"` sent a non-git directory down the pull branch.
        self.assertIn('[ -d "$INSTALL_DIR/.git" ]', self.source)

    def test_verification_uses_the_installed_path(self):
        self.assertIn('"$BIN_LINK" --help', self.source)
        self.assertNotIn("if aictl --help", self.source)

    def test_sudo_is_skipped_when_already_root(self):
        self.assertIn('[ "$(id -u)" -ne 0 ]', self.source)

    def test_version_check_compares_the_pair(self):
        # Source-level only for the positive form; the negative was originally
        # written as assertNotIn of the old expression and failed on the
        # comment that *quotes* it while explaining the bug. That is the fifth
        # time this session a substring check has caught prose instead of
        # behaviour, so the real property is asserted behaviourally in
        # TestDetectPython.test_a_future_major_version_is_accepted and in the
        # boundary test below.
        self.assertIn('[ "$major" -gt 3 ]', self.source)

    def test_paths_are_overridable_so_this_is_testable(self):
        # A script with no seams cannot be tested without a real install,
        # which is why none of the above was ever caught.
        for var in ("AIOS_INSTALL_DIR", "AIOS_BIN_LINK", "AIOS_INSTALL_LIB"):
            self.assertIn(var, self.source)

    def test_sourcing_does_not_install(self):
        # The guard that makes the tests above safe to run.
        result = _bash('echo SOURCED_ONLY')
        self.assertIn("SOURCED_ONLY", result.stdout)
        self.assertNotIn("Installer", result.stdout)

    def test_script_is_valid_bash(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(INSTALLER)],
                                        capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
