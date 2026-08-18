"""Parallel test execution — cycle-time acceleration for `aictl gate`.

Measured, not assumed: of `aictl gate`'s ~59s, the test suite is ~57s. Every
other phase — compile, import, version, demo, docs, MCP, security — costs 2.8s
combined. So "make the gate faster" means exactly "make the suite faster", and
nothing else is worth touching.

The suite is 3734 tests over 273 files, run serially in one process. Running
each *file* in its own process recovers most of that: files are already
independent of one another (a process boundary is the strongest isolation
there is), while test order *within* a file is preserved, which matters
because unittest orders methods alphabetically and some tests rely on it.

Two properties this deliberately does not trade away:

* **Isolation, not just speed.** Each worker gets its own state directory.
  Without that, parallel workers race on ~/.aios — and worse, the suite would
  keep writing to the user's real state directory, which was a bug found
  earlier in this codebase. Speed and hermeticity come from the same change.
* **Serial remains the source of truth.** This is opt-in. `aictl gate` still
  runs the suite serially by default, because a parallel run can only ever be
  evidence *about* the serial result, and the discipline of this project is
  that the gate's verdict is the real one.

Threads rather than processes for the pool: the work is `subprocess.run`, so
the GIL is released for essentially the whole duration and a thread pool
avoids paying fork cost twice.
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParallelResult:
    """Outcome of a parallel suite run."""
    passed: int = 0
    failed: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    workers: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, object]:
        return {
            "passed_files": self.passed,
            "failed_files": self.failed,
            "elapsed_s": round(self.elapsed_s, 1),
            "workers": self.workers,
        }


def default_workers() -> int:
    """Leave a core free so the machine stays usable during a run."""
    return max(1, min(16, (os.cpu_count() or 2) - 1)) if (os.cpu_count() or 2) > 2 \
        else 2


def discover_test_files(tests_dir: Path | None = None) -> list[str]:
    """Module names for every test file, sorted for reproducible scheduling."""
    root = tests_dir or Path("tests")
    return sorted(p.stem for p in root.glob("test_*.py"))


def _run_one(name: str, timeout_s: float) -> tuple[str, int, str]:
    """Run one test module in its own process and state directory."""
    env = dict(os.environ)
    state = tempfile.mkdtemp(prefix="aictl-partest-")
    # Both names are in use across the codebase; set both so no module falls
    # back to the user's real ~/.aios.
    env["AICTL_STATE_DIR"] = state
    env["AIOS_STATE_DIR"] = state
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", f"tests.{name}"],
            capture_output=True, text=True, env=env, timeout=timeout_s,
        )
        return name, proc.returncode, proc.stderr
    except subprocess.TimeoutExpired:
        return name, 1, f"timed out after {timeout_s}s"
    except Exception as e:                       # never let one file abort the run
        return name, 1, str(e)[:200]


def run_parallel(workers: int = 0, timeout_s: float = 300.0,
                 tests_dir: Path | None = None) -> ParallelResult:
    """Run every test file concurrently. Returns which files failed."""
    files = discover_test_files(tests_dir)
    n = workers or default_workers()
    result = ParallelResult(workers=n)
    start = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        for name, code, _err in pool.map(lambda f: _run_one(f, timeout_s), files):
            if code == 0:
                result.passed += 1
            else:
                result.failed.append(name)

    result.elapsed_s = time.time() - start
    return result
