"""Pass 84 (Socratic round 4): end-to-end CLI contract guard.

Rounds 2 and 3 each found a whole CLASS of bug that mocked unit tests missed
because no test drove commands through the real main() entrypoint with the
*global* flags:
  - round 2: 32 subcommands silently defeated `aictl --json <cmd>`
  - round 3: ~24 commands crashed on `aictl --state-dir DIR <cmd>` (str.mkdir)
  - round 4: more empty-state `--json` paths emitted human text (context list…)

Rather than keep fixing instances, this module is the NET: it drives a broad set
of read-only commands through the actual main() with both global flags and
asserts the two cross-cutting contracts — (1) no command crashes, and (2)
`--json` commands emit parseable JSON — so the entire class is caught going
forward. Each invocation runs in an isolated, empty state dir for determinism.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

# One STABLE state dir for the whole module. Per-invocation TemporaryDirectory
# would be deleted between calls, dangling module-level singletons (e.g. the
# sem_cache _DEFAULT_CACHE) that cache a db_path — a test artifact, not a product
# bug. A single persistent dir mirrors real usage (one state dir per process).
_SHARED_SD = None


def setUpModule() -> None:
    global _SHARED_SD
    _SHARED_SD = tempfile.mkdtemp(prefix="aictl-e2e-")
    os.environ["AIOS_STATE_DIR"] = _SHARED_SD


def tearDownModule() -> None:
    os.environ.pop("AIOS_STATE_DIR", None)
    if _SHARED_SD:
        shutil.rmtree(_SHARED_SD, ignore_errors=True)

# Read-only invocations that must never crash under the global flags.
SMOKE_CMDS = [
    "status", "ps", "info", "recommend", "engines list", "model list",
    "snapshot list", "context list", "warmup stats", "lora list", "cache status",
    "config show", "audit", "audit stats", "events list", "events types", "perf",
    "cost compare", "cost providers", "tco forecast", "recipe list",
    "bench history", "quota report", "meter report", "node list", "tenant list",
    "apikey list", "plugin list", "hooks list", "alert rules", "scale status",
]

# Subset that must emit valid, parseable JSON under --json (the listing/report
# commands). Excludes status-style commands that legitimately print nothing.
JSON_CMDS = [
    "status", "ps", "info", "recommend", "engines list", "model list",
    "snapshot list", "context list", "warmup stats", "lora list", "cache status",
    "config show", "audit", "audit stats", "events list", "events types", "perf",
    "cost compare", "cost providers", "tco forecast", "recipe list",
    "bench history", "quota report", "meter report", "node list", "tenant list",
    "apikey list", "plugin list", "hooks list", "alert rules",
]

# Substrings that signal an uncaught exception surfaced through main()'s handler.
_CRASH_MARKERS = ("Unexpected error", "Traceback (most recent call last)",
                  "has no attribute", "'str' object", "'NoneType' object")


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Drive the real main() against the shared stable state dir; capture rc/out/err."""
    from aictl.__main__ import main
    out, errbuf = io.StringIO(), io.StringIO()
    full = ["aictl", "--json", "--state-dir", _SHARED_SD] + argv
    with patch.object(sys, "argv", full), \
         redirect_stdout(out), redirect_stderr(errbuf):
        rc = main()
    return rc, out.getvalue(), errbuf.getvalue()


class TestNoCommandCrashes(unittest.TestCase):
    """`aictl --json --state-dir DIR <cmd>` must never surface an exception."""

    def test_smoke_no_crash(self):
        for cmd in SMOKE_CMDS:
            with self.subTest(cmd=cmd):
                rc, out, err = _run(cmd.split())
                blob = out + err
                for marker in _CRASH_MARKERS:
                    self.assertNotIn(marker, blob,
                                     f"`aictl --json --state-dir X {cmd}` crashed: {blob[:200]}")


class TestJsonContract(unittest.TestCase):
    """`--json` read-only commands must emit parseable JSON via the global form."""

    def test_json_is_parseable(self):
        for cmd in JSON_CMDS:
            with self.subTest(cmd=cmd):
                rc, out, _ = _run(cmd.split())
                self.assertEqual(rc, 0, f"{cmd} returned rc={rc}")
                try:
                    json.loads(out)
                except json.JSONDecodeError as e:
                    self.fail(f"`aictl --json {cmd}` emitted non-JSON: {out[:200]!r} ({e})")


class TestMutatingJsonContract(unittest.TestCase):
    """State-changing commands must also emit parseable JSON under --json.

    The read-only net (above) does not exercise mutations, so round 5 found
    config set/reset, quota create/reset, context save, and warmup cancel all
    emitting human text under --json. These run in sequence against the shared
    state dir (create-before-read ordering matters).
    """

    # (argv, must-be-valid-json) executed in order against the shared state dir.
    MUTATIONS = [
        "config set log_level debug",
        "config reset",
        "quota create netcheck-team --tokens-per-month 1000",
        "quota reset netcheck-team --yes",
        "context save",
        "warmup schedule --every 1h",
        "warmup cancel",
        "snapshot create --label netcheck",
        "model register netcheck-model:7b",
        "apikey create netcheck-key",
    ]

    def test_mutations_emit_json(self):
        for cmd in self.MUTATIONS:
            with self.subTest(cmd=cmd):
                rc, out, err = _run(cmd.split())
                for marker in _CRASH_MARKERS:
                    self.assertNotIn(marker, out + err, f"{cmd} crashed")
                try:
                    json.loads(out)
                except json.JSONDecodeError as e:
                    self.fail(f"`aictl --json {cmd}` emitted non-JSON: {out[:200]!r} ({e})")


if __name__ == "__main__":
    unittest.main()
