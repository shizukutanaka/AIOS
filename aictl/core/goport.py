"""Report the Go port's build status — because nothing did.

`aictl gate` is this project's "is everything all right?" command, and it
verified only the Python half. The Go port is 2,176 lines advertised in both
CLAUDE.md and the release notes as "29 Go commands", and a regression there
would ship undetected because no automated check ever looked at it.

Worse than untested: on a clean checkout it does not build at all. `go.sum`
records a checksum for github.com/spf13/cobra v1.8.1 that disagrees with what
the module proxy serves, so Go refuses with a SECURITY ERROR. The two hashes
share a 38-character prefix and differ only in the tail — independent hashes
differ everywhere, so that signature points to a corrupted or hand-edited
go.sum entry rather than a substituted artifact.

This module deliberately does not fix that. Rewriting go.sum with whatever the
proxy happened to serve is precisely the supply-chain control go.sum exists to
provide, and the authoritative value could not be checked because
sum.golang.org is unreachable from this environment. Reporting an unverifiable
state honestly is the correct outcome; silently "fixing" it would not be.

So: surface the status. A gap that shows up in the gate's output can be acted
on. One that does not exist in the gate's output looks like health.

Never fails the gate on its own. Go being absent, or a module proxy being
unreachable, is a property of the machine rather than of the code — the same
reasoning the security phase already uses for host findings.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Long enough for a cold module download on a slow link, short enough that a
# hung proxy cannot stall the gate indefinitely.
GO_BUILD_TIMEOUT_S = 120


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
