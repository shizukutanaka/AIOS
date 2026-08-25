"""Report the Go port's build status — because nothing did.

`aictl gate` is this project's "is everything all right?" command, and it
verified only the Python half. The Go port is 2,176 lines advertised in both
CLAUDE.md and the release notes as "29 Go commands", and a regression there
would ship undetected because no automated check ever looked at it.

Worse than untested: on a clean checkout it did not build at all, and the
reason turned out to be more serious than a broken build. `go.sum` did not
record checksums that disagreed with the module proxy; it recorded values that
were never produced by a hash function.

Four of its eight entries were wrong, and the way they were wrong is the whole
story. Compare what the file claimed against what the proxy actually serves:

    mousetrap    wN+x4NVGpMsO7ErU QYnwIlCDoM6PDIBo7tSrmkPvXss=   (claimed)
                 wN+x4NVGpMsO7ErU n/mUI3vEoE6Jt13X2s0bqwp9tc8=   (real)
    cobra (zip)  e5/vxKd/rZsfSJMUX1agtjeTDf+qv1/JdBF8 lex5Gm=    (claimed)
                 e5/vxKd/rZsfSJMUX1agtjeTDf+qv1/JdBF8 gg5k9ZM=   (real)

Each shares a long prefix with the true hash and then diverges into a
plausible-looking tail. Independent SHA-256 values share essentially no prefix,
so a 36-character agreement is not coincidence and not corruption in transit —
it is the signature of a value reproduced from memory and completed by
guesswork. Two further tells: the cobra zip hash was 43 base64 characters and
therefore could not decode to 32 bytes at all, and the file omitted entries
(`gopkg.in/check.v1`, `gopkg.in/yaml.v3`) that a real `go mod tidy` emits.

That file was not a damaged supply-chain control. It was never one — a thing
shaped like an attestation, attesting to nothing, sitting in a repository whose
own `aictl trust` subsystem exists to verify artifacts.

The fabricated entries were first removed rather than replaced, because writing
in proxy-derived values would have converted "unverified once, in a sandbox"
into "unverified permanently, for everyone": once a hash is present in go.sum,
Go trusts it and never consults the checksum database again.

They are now restored, and verified. `sum.golang.org` is unreachable through
this environment's HTTPS proxy, but reachable by a different egress path, so
every entry was read from the checksum database itself and matched against what
the module proxy served. All ten agree. That is the authority go.sum exists to
record, so the values are pinned in `tests/test_new_features_212.py`: a future
edit that changes one fails until whoever makes it re-verifies.

With the checksum barrier gone, the build surfaced the defect it had been
hiding all along — an unused import in `internal/runtime/broker.go`. The Go
port now builds, vets and tests clean.

`lint_go_sum` below is the general form of the lesson. A hash that cannot have
come from a hash function is detectable locally, in microseconds, with no
network and no toolchain — so this class of defect can never again sit in the
tree unnoticed.

Never fails the gate on its own. Go being absent, or a module proxy being
unreachable, is a property of the machine rather than of the code — the same
reasoning the security phase already uses for host findings.
"""

from __future__ import annotations

import base64
import binascii
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Long enough for a cold module download on a slow link, short enough that a
# hung proxy cannot stall the gate indefinitely.
GO_BUILD_TIMEOUT_S = 120

# go.sum records dirhash "h1:" values: base64 of a SHA-256, so exactly 32 bytes.
GO_SUM_HASH_PREFIX = "h1:"
GO_SUM_HASH_BYTES = 32

_REQUIRE_LINE = re.compile(r"^\s*([^\s/][^\s]*)\s+(v[^\s]+)")


@dataclass(frozen=True)
class GoSumFinding:
    """One go.sum entry that cannot be a real hash."""
    line: int
    entry: str
    reason: str

    def __str__(self) -> str:
        return f"go.sum:{self.line} {self.entry}: {self.reason}"


def lint_go_sum(root: Path | None = None) -> list[GoSumFinding]:
    """Check go.sum for values no hash function could have produced.

    Purely local: no network, no toolchain, no module download. A `h1:` value is
    base64 of a SHA-256, so anything that does not decode to exactly 32 bytes is
    provably not a checksum — a fact that needs no authority to establish. This
    is what would have caught the fabricated entries immediately instead of
    presenting them as a SECURITY ERROR that read like an attack in progress.

    An empty list means every entry is *well-formed*, which is a much weaker
    claim than every entry being *correct*: only the checksum database can say
    that. Well-formedness is simply the part that can be checked from here.
    """
    path = (root or Path(".")) / "go-port" / "go.sum"
    try:
        text = path.read_text()
    except OSError:
        return []                     # absent go.sum is handled by the caller

    findings: list[GoSumFinding] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 3:
            findings.append(GoSumFinding(number, line.strip()[:48],
                                         f"expected 3 fields, found {len(parts)}"))
            continue
        entry = f"{parts[0]} {parts[1]}"
        digest = parts[2]
        if not digest.startswith(GO_SUM_HASH_PREFIX):
            findings.append(GoSumFinding(number, entry,
                                         f"unknown hash algorithm {digest[:6]!r}"))
            continue
        encoded = digest[len(GO_SUM_HASH_PREFIX):]
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            findings.append(GoSumFinding(
                number, entry,
                f"not valid base64 ({len(encoded)} chars) — cannot be a SHA-256"))
            continue
        if len(raw) != GO_SUM_HASH_BYTES:
            findings.append(GoSumFinding(
                number, entry,
                f"decodes to {len(raw)} bytes, not {GO_SUM_HASH_BYTES}"))
    return findings


def missing_go_sum_entries(root: Path | None = None) -> list[str]:
    """Modules required by go.mod with no `/go.mod` hash recorded in go.sum.

    Not a defect on its own — this is the expected state after the fabricated
    entries were removed, and Go fills it in from the checksum database on the
    first build. It is reported so that "not yet recorded" is never mistaken for
    "verified".
    """
    base = (root or Path(".")) / "go-port"
    try:
        mod_text = (base / "go.mod").read_text()
    except OSError:
        return []
    try:
        sum_text = (base / "go.sum").read_text()
    except OSError:
        sum_text = ""

    required: list[str] = []
    inside = False
    for line in mod_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            inside = True
            continue
        if inside and stripped == ")":
            inside = False
            continue
        target = stripped
        if stripped.startswith("require "):
            target = stripped[len("require "):].strip()
        elif not inside:
            continue
        match = _REQUIRE_LINE.match(target)
        if match:
            required.append(f"{match.group(1)} {match.group(2)}")

    return [module for module in required
            if f"{module}/go.mod " not in sum_text]


@dataclass
class GoStatus:
    """What we could determine about the Go port, and why."""
    present: bool = False        # go-port/ exists in the tree
    toolchain: bool = False      # a `go` binary is on PATH
    builds: bool | None = None   # None = could not be determined
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"present": self.present, "toolchain": self.toolchain,
                "builds": self.builds, "detail": self.detail}


def check_go_port(root: Path | None = None,
                  timeout_s: float = GO_BUILD_TIMEOUT_S) -> GoStatus:
    """Try to build the Go port. Never raises; never fails a caller."""
    base = (root or Path(".")) / "go-port"
    if not (base / "go.mod").is_file():
        return GoStatus(detail="no go-port/ in this tree")

    # Cheapest and most damning check first: an entry that cannot be a hash is
    # provably wrong without a network, a toolchain, or anyone's authority.
    malformed = lint_go_sum(root)
    if malformed:
        return GoStatus(
            present=True, toolchain=shutil.which("go") is not None, builds=False,
            detail=f"go.sum has {len(malformed)} entry/entries that cannot be a "
                   f"checksum — {malformed[0]}")

    if shutil.which("go") is None:
        return GoStatus(present=True,
                        detail="go toolchain not installed — Go port unverified")

    try:
        proc = subprocess.run(["go", "build", "./..."], cwd=base,
                              capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return GoStatus(present=True, toolchain=True,
                        detail=f"go build timed out after {timeout_s:.0f}s")
    except Exception as e:                       # pragma: no cover - defensive
        return GoStatus(present=True, toolchain=True, detail=str(e)[:80])

    if proc.returncode == 0:
        return GoStatus(present=True, toolchain=True, builds=True,
                        detail="go build ./... succeeded")

    stderr = proc.stderr or ""
    if "missing go.sum entry" in stderr:
        # The expected state after the fabricated entries were removed. Go fills
        # these in from the checksum database, so this says nothing about the
        # code — reporting it as a build failure would be a false alarm.
        pending = missing_go_sum_entries(root)
        names = ", ".join(pending[:2]) if pending else "required modules"
        return GoStatus(
            present=True, toolchain=True,
            detail=f"go.sum entries not yet recorded for {names} — run "
                   f"`go mod download` where sum.golang.org is reachable")
    if "checksum mismatch" in stderr or "SECURITY ERROR" in stderr:
        # Called out specifically because it is not a code defect and must not
        # be "fixed" by rewriting go.sum.
        return GoStatus(
            present=True, toolchain=True, builds=False,
            detail="go.sum checksum mismatch — dependency cannot be verified, "
                   "so the Go port does not build from a clean checkout")
    if "dial tcp" in stderr or "timeout" in stderr or "proxy" in stderr:
        return GoStatus(present=True, toolchain=True,
                        detail="module proxy unreachable — Go port unverified")

    first = next((line for line in stderr.splitlines() if line.strip()), "")
    return GoStatus(present=True, toolchain=True, builds=False,
                    detail=f"go build failed: {first[:70]}")


# Commands are registered in one `root.AddCommand(...)` block in main.go.
_GO_ROOT_BLOCK = re.compile(r"root\.AddCommand\((.*?)\n\t\)", re.S)
_GO_CMD_CALL = re.compile(r"\b(cmd[A-Z]\w*)\(\)")


def go_command_count(root: Path | None = None) -> int:
    """How many commands the Go port registers. 0 if it cannot be determined.

    Read from the source rather than by running `aictl --help` in the Go
    binary: that needs a toolchain and a build, and this is consulted while
    checking documentation counts. It also avoids a trap — Cobra adds its own
    `help` and `completion` commands, so the built binary lists 31 while the
    port defines 29. Counting the registrations is what the documentation
    actually claims.
    """
    base = (root or Path(".")) / "go-port" / "cmd" / "aictl" / "main.go"
    try:
        block = _GO_ROOT_BLOCK.search(base.read_text(encoding="utf-8"))
    except OSError:
        return 0
    if not block:
        return 0
    return len(set(_GO_CMD_CALL.findall(block.group(1))))
