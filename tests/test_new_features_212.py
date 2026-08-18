"""Pass 212: the go.sum was fabricated, and removing it is the safe repair.

Pass 211 stopped at "the Go port does not build, and the checksum could not be
verified from here" — correct, and incomplete. Two questions had gone unasked.

The first was whether the recorded value was even *shaped* like a hash. It was
not: 43 base64 characters, which cannot decode to a 32-byte SHA-256. That is
decidable locally, in microseconds, with no network and nobody's authority —
and it means the entry was not a competing attestation to be weighed against
the proxy's. It was not an attestation at all.

The second was how far the damage went. Four of eight entries were wrong, and
the shape of the wrongness is the finding:

    mousetrap    wN+x4NVGpMsO7ErU QYnwIlCDoM6PDIBo7tSrmkPvXss=   claimed
                 wN+x4NVGpMsO7ErU n/mUI3vEoE6Jt13X2s0bqwp9tc8=   real
    blackfriday  +Rmxgy9KzJVeS9/2gXHxylqXiyQDYRxCVz55j GbOGsM=   claimed
                 +Rmxgy9KzJVeS9/2gXHxylqXiyQDYRxCVz55j meOWTM=   real

Long shared prefix, then a plausible-looking tail. Independent SHA-256 values
share essentially no prefix, so 36 characters of agreement is not chance and
not corruption in transit — it is a value reproduced from memory and finished
by guesswork. The file also omitted entries a real `go mod tidy` emits.

**Removed rather than replaced, and the direction is the whole point.** Once a
hash sits in go.sum, Go trusts it and never consults the checksum database
again. Writing in proxy-derived values would therefore have made the build pass
by converting "unverified once, in a sandbox" into "unverified permanently, for
everyone" — the strictly worse outcome, and the tempting one, because it is the
one that turns the gate green. With the entries gone, Go *must* ask
sum.golang.org and records a verified hash on the first build from any machine
that can reach it. Removal requires trusting nothing. That is why it is safe.

The generalisable half is `lint_go_sum`: a hash that cannot have come from a
hash function is detectable without a network, a toolchain, or a build. This
project ships `aictl trust` to verify other people's artifacts; it had no check
at all on its own.
"""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from aictl.core.goport import (
    GO_SUM_HASH_BYTES,
    check_go_port,
    lint_go_sum,
    missing_go_sum_entries,
)

# The real, proxy-served hash for cobra v1.8.1 — well-formed, used as a fixture
# for "this is what a valid line looks like". Nothing here asserts it is the
# *correct* hash for that module; only the checksum database can say that.
VALID = "h1:e5/vxKd/rZsfSJMUX1agtjeTDf+qv1/JdBF8gg5k9ZM="

# The value the repository actually shipped: 43 characters, so not a SHA-256.
FABRICATED = "h1:e5/vxKd/rZsfSJMUX1agtjeTDf+qv1/JdBF8lex5Gm="


def _tree(root: Path, go_sum: str | None, go_mod: str = "") -> Path:
    (root / "go-port").mkdir(parents=True, exist_ok=True)
    (root / "go-port" / "go.mod").write_text(
        go_mod or "module example.com/x\n\ngo 1.23\n")
    if go_sum is not None:
        (root / "go-port" / "go.sum").write_text(go_sum)
    return root


class TestFabricatedHashesAreDetectable(unittest.TestCase):
    """No network, no toolchain, no authority — just arithmetic."""

    def test_the_shipped_value_is_not_a_sha256(self):
        # 43 base64 characters cannot encode 32 bytes. This is the fact that
        # made the whole diagnosis possible from an egress-blocked sandbox.
        with self.assertRaises(Exception):
            base64.b64decode(FABRICATED[3:], validate=True)

    def test_the_real_value_decodes_to_32_bytes(self):
        self.assertEqual(len(base64.b64decode(VALID[3:], validate=True)),
                         GO_SUM_HASH_BYTES)

    def test_lint_flags_the_fabricated_entry(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), f"github.com/spf13/cobra v1.8.1 {FABRICATED}\n")
            findings = lint_go_sum(root)
            self.assertEqual(len(findings), 1)
            self.assertIn("cobra", findings[0].entry)

    def test_finding_explains_why_not_just_that(self):
        # "Malformed" alone would send a reader to the network. Saying it is
        # 43 characters and cannot be a SHA-256 ends the investigation.
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), f"m v1 {FABRICATED}\n")
            message = str(lint_go_sum(root)[0])
            self.assertIn("43", message)
            self.assertIn("SHA-256", message)

    def test_finding_names_the_line_number(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td),
                         f"a v1 {VALID}\nb v2 {VALID}\nc v3 {FABRICATED}\n")
            self.assertEqual(lint_go_sum(root)[0].line, 3)

    def test_well_formed_file_yields_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), f"a v1 {VALID}\na v1/go.mod {VALID}\n")
            self.assertEqual(lint_go_sum(root), [])

    def test_wrong_length_hash_is_caught(self):
        # Valid base64, but of 16 bytes rather than 32 — decodes cleanly and is
        # still not a SHA-256, so validity alone would not have caught it.
        short = "h1:" + base64.b64encode(b"x" * 16).decode()
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), f"a v1 {short}\n")
            self.assertIn("16 bytes", str(lint_go_sum(root)[0]))

    def test_unknown_algorithm_is_caught(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), "a v1 h9:zzzz=\n")
            self.assertIn("unknown hash algorithm", str(lint_go_sum(root)[0]))

    def test_malformed_line_shape_is_caught(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), "just-two-fields v1\n")
            self.assertIn("3 fields", str(lint_go_sum(root)[0]))

    def test_blank_lines_are_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), f"\n\na v1 {VALID}\n\n")
            self.assertEqual(lint_go_sum(root), [])

    def test_absent_go_sum_is_not_a_finding(self):
        # Absence is the deliberate post-fix state, not a malformed entry.
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(lint_go_sum(_tree(Path(td), None)), [])

    def test_missing_tree_is_not_a_finding(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(lint_go_sum(Path(td)), [])


class TestThisRepositoryIsClean(unittest.TestCase):
    """The regression guard. This is what shipped broken."""

    def test_no_malformed_entries_in_this_tree(self):
        findings = lint_go_sum(Path("."))
        self.assertEqual([str(f) for f in findings], [])

    def test_the_fabricated_file_is_gone(self):
        # Deliberately absent: see go-port/README.md. If someone restores a
        # go.sum it must be one the toolchain wrote, and the lint above will
        # be checking it from then on.
        self.assertFalse(Path("go-port/go.sum").exists(),
                         "go.sum is back — was it generated by `go mod "
                         "download`, or written by hand?")

    def test_the_reasoning_is_recorded_where_a_maintainer_will_look(self):
        # A deleted file with no explanation invites someone to "restore" it.
        readme = Path("go-port/README.md").read_text()
        self.assertIn("go mod download", readme)
        self.assertIn("checksum database", readme)


class TestRemovalRestoresVerification(unittest.TestCase):
    """The property that makes removal the safe direction rather than a dodge."""

    def test_required_modules_are_reported_as_unrecorded(self):
        go_mod = ("module example.com/x\n\ngo 1.23\n\n"
                  "require (\n\tgithub.com/spf13/cobra v1.8.1\n)\n")
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), "", go_mod)
            self.assertIn("github.com/spf13/cobra v1.8.1",
                          missing_go_sum_entries(root))

    def test_recorded_modules_are_not_reported(self):
        go_mod = ("module example.com/x\n\ngo 1.23\n\n"
                  "require (\n\tgithub.com/spf13/cobra v1.8.1\n)\n")
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td),
                         f"github.com/spf13/cobra v1.8.1/go.mod {VALID}\n", go_mod)
            self.assertEqual(missing_go_sum_entries(root), [])

    def test_single_line_require_is_understood(self):
        go_mod = "module x\n\ngo 1.23\n\nrequire github.com/spf13/pflag v1.0.5\n"
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), "", go_mod)
            self.assertIn("github.com/spf13/pflag v1.0.5",
                          missing_go_sum_entries(root))

    def test_this_repo_still_requires_cobra(self):
        # Sanity: the removal took go.sum, not go.mod.
        self.assertIn("github.com/spf13/cobra v1.8.1",
                      missing_go_sum_entries(Path(".")))

    def test_missing_go_mod_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(missing_go_sum_entries(Path(td)), [])


class TestStatusReporting(unittest.TestCase):
    def test_malformed_go_sum_is_reported_without_a_build(self):
        # It must not need the toolchain: the point of the lint is that this
        # is decidable locally, so a Go-less machine still catches it.
        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), f"a v1 {FABRICATED}\n")
            status = check_go_port(root)
            self.assertIs(status.builds, False)
            self.assertIn("cannot be a checksum", status.detail)

    def test_unrecorded_entries_are_undetermined_not_broken(self):
        # The distinction this pass turns on: "not yet verified" is not a
        # claim that the code is broken, and reporting it as a build failure
        # would be the same undisclosed-degradation mistake in reverse.
        status = check_go_port(Path("."))
        self.assertIsNot(status.builds, False,
                         "absent checksums say nothing about the Go code")

    def test_real_tree_status_points_at_the_remedy(self):
        detail = check_go_port(Path(".")).detail
        if "not yet recorded" in detail:
            self.assertIn("go mod download", detail)

    def test_lint_needs_no_network_or_toolchain(self):
        # Guards the property that makes it usable in the gate on every run.
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            root = _tree(Path(td), f"a v1 {FABRICATED}\n")
            with patch("aictl.core.goport.subprocess.run",
                       side_effect=AssertionError("lint must not shell out")):
                self.assertTrue(lint_go_sum(root))
                self.assertIs(check_go_port(root).builds, False)


class TestNoUnverifiedHashIsEverWritten(unittest.TestCase):
    """The refusal this pass is built around, pinned so it cannot erode."""

    def test_no_module_writes_go_sum(self):
        from tests.test_new_features_211 import _nearby

        for module in (Path("aictl/core/goport.py"), Path("aictl/cmd/gate.py")):
            source = module.read_text()
            for writer in ("write_text", "unlink", "shutil.copy"):
                if writer in source:
                    self.assertNotIn("go.sum", _nearby(source, writer),
                                     f"{module} appears to write go.sum")

    def test_verification_is_never_disabled(self):
        # GOSUMDB=off / GONOSUMDB / GOPRIVATE would make the build pass by
        # switching off the checksum database. That is the shortcut this pass
        # exists to refuse, so no shipped code may reach for it.
        for module in (Path("aictl/core/goport.py"), Path("aictl/cmd/gate.py")):
            source = module.read_text()
            for escape in ("GOSUMDB", "GONOSUMDB", "GOPRIVATE", "GOFLAGS"):
                self.assertNotIn(escape, source,
                                 f"{module} tampers with Go's verification")


if __name__ == "__main__":
    unittest.main()
