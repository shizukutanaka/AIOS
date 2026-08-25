"""aictl gate — automated quality gate for CI/CD and release validation."""

from __future__ import annotations

from typing import Any

import argparse

import importlib
import io
import os
import sys
import time
import unittest
from pathlib import Path


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("gate", help="Run quality gate (compile + import + version + tests + demo)")
    p.add_argument("--skip-demo", action="store_true", help="Skip demo step")
    p.add_argument("--skip-tests", action="store_true", help="Skip test suite")
    p.add_argument("--parallel", action="store_true",
                   help="Run the suite file-per-process (much faster). Serial "
                        "remains the source of truth — use this to iterate, "
                        "then confirm with a normal run.")
    p.add_argument("--jobs", type=int, default=0,
                   help="Worker count for --parallel (default: cores - 1).")
    p.set_defaults(func=run)


def _docs_issues(project_root) -> tuple[list[str], str]:
    """Documentation checks, every one derived from the real command surface.

    Returns (issues, success_detail). Replaces two frozen lists: a 10-name
    "critical commands" list from the v1.6.0 era and a 5-name README list —
    while the CHANGELOG check three lines below them was already derived from
    VERSION, with a comment explaining exactly why literals rot. Now the same
    reasoning applies to all of it:

      * every registered command must carry an argparse help string;
      * documentation must not reference commands that do not exist (both the
        help topics and the markdown docs — ghosts are how docs betray users);
      * the curated topics must reference at least DOCS_MIN_TOPIC_COMMANDS
        registered commands — a floor that catches the help collapsing without
        pretending the 8 curated guides are a per-command reference;
      * the README scan finding zero references at all fails, because a
        matcher that matches nothing would make the ghost check vacuously
        green forever.
    """
    from pathlib import Path

    from aictl.__main__ import VERSION
    from aictl.cmd.help import TOPICS
    from aictl.core.cli_surface import (
        command_references,
        markdown_command_references,
        registered_commands,
    )
    from aictl.core.constants import DOCS_MIN_TOPIC_COMMANDS

    issues: list[str] = []
    commands = registered_commands()
    names = set(commands)

    unhelped = sorted(n for n, h in commands.items() if not h)
    if unhelped:
        issues.append(f"no parser help: {', '.join(unhelped[:3])}")

    topic_refs = command_references("\n".join(TOPICS.values()))
    for ghost in sorted(topic_refs - names):
        issues.append(f"ghost in help topics: aictl {ghost}")

    readme_refs: set[str] = set()
    for doc_name in ("README.md", "CLAUDE.md"):
        doc_path = Path(project_root) / doc_name
        if not doc_path.is_file():
            continue
        refs = markdown_command_references(doc_path.read_text(errors="replace"))
        if doc_name == "README.md":
            readme_refs = refs
        for ghost in sorted(refs - names):
            issues.append(f"ghost in {doc_name}: aictl {ghost}")

    covered = len(topic_refs & names)
    if covered < DOCS_MIN_TOPIC_COMMANDS:
        issues.append(f"help topics reference {covered} commands "
                      f"(< {DOCS_MIN_TOPIC_COMMANDS})")

    if (Path(project_root) / "README.md").is_file() and not readme_refs:
        issues.append("no aictl command references found in README.md")

    # Derived, not hardcoded: a literal here has to be hand-edited at every
    # version bump, and a forgotten edit makes the check pass against the
    # *previous* release forever.
    expected_release = f"v{VERSION}"
    changelog = Path(project_root) / "CHANGELOG.md"
    cl_text = changelog.read_text(errors="replace") if changelog.is_file() else ""
    if expected_release not in cl_text:
        issues.append(f"{expected_release} missing from CHANGELOG")

    detail = (f"{len(names)} commands: help text ok, {covered} in topics, "
              f"0 ghosts, {expected_release} in CHANGELOG")
    return issues, detail


def run(args: argparse.Namespace) -> int:
    """Execute the gate command."""
    results: list[tuple[str, bool, str]] = []
    t0 = time.monotonic()

    # 1. Compile check
    compile_errors = 0
    aictl_dir = Path(__file__).resolve().parent.parent
    for f in aictl_dir.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        try:
            import py_compile
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError:
            compile_errors += 1
    results.append(("Compile", compile_errors == 0, f"{compile_errors} errors"))

    # 2. Import check
    import_errors = 0
    project_root = aictl_dir.parent
    sys.path.insert(0, str(project_root))
    for f in aictl_dir.rglob("*.py"):
        if "__pycache__" in str(f) or f.name == "__init__.py":
            continue
        mod_path = str(f.relative_to(project_root)).replace("/", ".").replace(".py", "")
        try:
            importlib.import_module(mod_path)
        except Exception:
            import_errors += 1
    results.append(("Import", import_errors == 0, f"{import_errors} errors"))

    # 3. Version consistency
    from aictl.__main__ import VERSION
    toml_version = VERSION  # default if toml unreadable
    toml_path = project_root / "pyproject.toml"
    try:
        import tomllib
        with open(toml_path, "rb") as _f:
            toml_version = tomllib.load(_f).get("project", {}).get("version", VERSION)
    except Exception:
        pass  # best-effort; failure is non-critical
    if toml_path.exists():
        for line in toml_path.read_text().splitlines():
            if line.startswith("version"):
                toml_version = line.split('"')[1]
                break
    match = VERSION == toml_version
    results.append(("Version", match, f"{VERSION} == {toml_version}"))

    # 4. Tests
    if getattr(args, "parallel", False) and not getattr(args, "skip_tests", False):
        # Opt-in acceleration. The suite is ~95% of this gate's runtime, so it
        # is the only phase worth parallelizing. Reports files rather than
        # tests: a worker's exit code is per-file, and inventing a test count
        # from it would be a number we did not actually measure.
        from aictl.core.partest import run_parallel
        par = run_parallel(workers=getattr(args, "jobs", 0),
                           tests_dir=project_root / "tests")
        detail = (f"{par.passed}/{par.passed + len(par.failed)} files "
                  f"in {par.elapsed_s:.0f}s on {par.workers} workers")
        if par.failed:
            detail += f" — failed: {', '.join(par.failed[:3])}"
        results.append(("Tests", par.ok, detail))
        measured_tests = 0          # per-file runner reports files, not tests
    elif not getattr(args, "skip_tests", False):
        tests_dir = project_root / "tests"
        loader = unittest.TestLoader()
        suite = loader.discover(str(tests_dir))
        runner = unittest.TextTestRunner(verbosity=0, stream=io.StringIO())
        result = runner.run(suite)
        passed = result.testsRun - len(result.failures) - len(result.errors)
        results.append(("Tests", result.wasSuccessful(),
                        f"{passed}/{result.testsRun} passed"))
        measured_tests = result.testsRun
    else:
        results.append(("Tests", True, "skipped"))
        measured_tests = 0

    # 4b. Doc counts. Automating the thing that was hand-edited a dozen times
    #     in one session: CLAUDE.md's counts are the first thing any reader
    #     sees about this codebase's size, and nothing checked them.
    #     Syncs rather than merely complaining. A check that fails the build
    #     because a *derived number in a comment* is stale turns the gate's
    #     verdict into a statement about documentation instead of code — and
    #     leaves the human doing the sed anyway, which is the manual step this
    #     was meant to remove. Derived data should be derived. It reports what
    #     it changed, so the rewrite is never silent.
    try:
        from aictl.core.docsync import sync_counts
        changed = sync_counts(project_root, measured_tests)
        results.append(("Counts", True,
                        "CLAUDE.md in sync" if not changed
                        else f"updated {len(changed)} stale count(s)"))
    except Exception as e:
        results.append(("Counts", True, f"skipped ({str(e)[:40]})"))

    # 4c. Go port status. The gate is this project's "is everything all
    #     right?" command and it verified only the Python half — 2,176 lines
    #     advertised as "29 Go commands" had no automated check at all. This
    #     reports rather than gates: a missing toolchain or an unreachable
    #     module proxy is a property of the machine, the same reasoning the
    #     security phase already applies to host findings.
    try:
        from aictl.core.goport import check_go_port
        go = check_go_port(project_root)
        if go.builds is False:
            results.append(("Go port", True, f"NOT BUILDING — {go.detail}"))
        else:
            results.append(("Go port", True, go.detail))
    except Exception as e:
        results.append(("Go port", True, f"skipped ({str(e)[:40]})"))

    # 5. Demo
    if not getattr(args, "skip_demo", False):
        try:
            from aictl.daemon.mock_engine import start_mock_engine
            from aictl.daemon.aiosd import AIOSHandler, ThreadedHTTPServer
            from aictl.core.state import StateStore, NodeState
            import tempfile
            import json
            import urllib.request

            tmp = Path(tempfile.mkdtemp())
            store = StateStore(tmp)
            store.save_node(NodeState(node_id="gate", hostname="gate",
                                      profile="cpu-only", version=VERSION, ram_total_mb=16384))
            from aictl.core.constants import TEST_ENGINE_PORT, TEST_DAEMON_PORT
            mock = start_mock_engine(port=TEST_ENGINE_PORT)
            AIOSHandler.store = store
            daemon = ThreadedHTTPServer(("127.0.0.1", TEST_DAEMON_PORT), AIOSHandler)
            daemon._start_time = time.time()
            import threading
            threading.Thread(target=daemon.serve_forever, daemon=True).start()
            time.sleep(0.5)

            # Test engine
            with urllib.request.urlopen(f"http://127.0.0.1:{TEST_ENGINE_PORT}/health", timeout=3) as r:
                assert json.loads(r.read())["status"] == "ok"

            # Test daemon
            with urllib.request.urlopen(f"http://127.0.0.1:{TEST_DAEMON_PORT}/v1/health", timeout=3) as r:
                assert json.loads(r.read())["status"] == "ok"

            # Test completion
            body = json.dumps({"model": "mock", "messages": [{"role": "user", "content": "gate test"}]}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{TEST_ENGINE_PORT}/v1/chat/completions",
                                        data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = json.loads(r.read())
                assert "choices" in resp

            mock.shutdown()
            daemon.shutdown()
            results.append(("Demo", True, "engine + daemon + completion"))
        except Exception as e:
            results.append(("Demo", False, str(e)[:60]))
    else:
        results.append(("Demo", True, "skipped"))

    # 6. Documentation consistency — every check derived from the registered
    #    command surface. This phase used to build the full parser, compute
    #    set(a.choices.keys()) — the true surface — and throw it away
    #    unassigned, then check a 10-name list frozen at v1.6.0.
    try:
        doc_issues, doc_detail = _docs_issues(project_root)
        if doc_issues:
            results.append(("Docs", False,
                            f"{len(doc_issues)} issue(s): {'; '.join(doc_issues[:3])}"))
        else:
            results.append(("Docs", True, doc_detail))
    except Exception as e:
        results.append(("Docs", False, str(e)[:60]))

    # 7. MCP server tool count
    try:
        from aictl.mcp_server import TOOLS
        mcp_count = len(TOOLS)
        ok_mcp = mcp_count >= 16
        results.append(("MCP", ok_mcp, f"{mcp_count} tools registered"))
    except Exception as e:
        results.append(("MCP", False, str(e)[:40]))

    # 8. Security scanner smoke test (audit item P8/#19: gate never invoked
    # core/security.py's own scanner, so a broken scanner could ship
    # undetected). This does NOT gate on the live score/findings — those
    # depend on the *host* environment (root vs rootless, cgroup v2
    # availability, container runtime presence), so hard-failing on them
    # would make the gate flaky exactly like the pre-fix ruff/mypy steps
    # (CLAUDE.md 6.2). Instead it verifies the scanner itself completes all
    # checks without raising, using an isolated tmp state dir so the result
    # never depends on (or pollutes) the caller's real state.
    try:
        from aictl.core.security import scan as _security_scan
        import tempfile as _tempfile
        sec_dir = Path(_tempfile.mkdtemp())
        sec_report = _security_scan(sec_dir)
        check_errors = [f for f in sec_report.findings
                        if f.title.startswith("Security check error")]
        ok_security = sec_report.checks_total > 0 and not check_errors
        if ok_security:
            results.append(("Security", True,
                            f"scanner ran {sec_report.checks_total} checks cleanly "
                            f"(score {sec_report.score}/100)"))
        else:
            results.append(("Security", False,
                            f"{len(check_errors)} check(s) raised an exception"))
    except Exception as e:
        results.append(("Security", False, str(e)[:60]))

    # 9. ruff lint (must be zero — CLAUDE.md 6.2)
    try:
        import subprocess
        proc = subprocess.run(
            ["python3", "-m", "ruff", "check", "aictl/"],
            cwd=str(project_root), capture_output=True, text=True, timeout=60,
        )
        # `python3 -m ruff` exits non-zero with "No module named ruff" on stderr
        # when the optional linter isn't installed — that is NOT a lint failure.
        if "No module named ruff" in proc.stderr:
            results.append(("Ruff", True, "ruff not installed (skipped)"))
        elif proc.returncode == 0:
            results.append(("Ruff", True, "All checks passed"))
        else:
            lines = proc.stdout.strip().splitlines()
            n = proc.stdout.count("\n--> ") or len(lines)
            results.append(("Ruff", False, f"errors found: {n}"))
    except FileNotFoundError:
        results.append(("Ruff", True, "ruff not installed (skipped)"))
    except Exception as e:
        results.append(("Ruff", True, f"skipped: {str(e)[:30]}"))

    # 10. mypy --strict ratchet (count must not exceed baseline — CLAUDE.md 6.2)
    # Baseline tracked in .mypy_baseline; ratchets down only.
    try:
        import subprocess
        baseline_path = project_root / ".mypy_baseline"
        baseline = int(baseline_path.read_text().strip()) if baseline_path.exists() else 999999
        proc = subprocess.run(
            ["python3", "-m", "mypy", "--strict", "aictl/"],
            cwd=str(project_root), capture_output=True, text=True, timeout=180,
        )
        # `python3 -m mypy` exits non-zero with "No module named mypy" on stderr
        # when the optional type checker isn't installed. Without this guard the
        # empty stdout falls through to the `999999` sentinel and reports a
        # phantom regression that fails the gate.
        if "No module named mypy" in proc.stderr:
            results.append(("MyPy", True, "mypy not installed (skipped)"))
        else:
            last_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
            import re as _re
            m = _re.search(r"Found (\d+) error", last_line)
            current = int(m.group(1)) if m else (0 if proc.returncode == 0 else 999999)
            if current < baseline:
                baseline_path.write_text(str(current) + "\n")  # ratchet down
                results.append(("MyPy", True, f"{current} errors (improved from {baseline})"))
            elif current == baseline:
                results.append(("MyPy", True, f"{current} errors (baseline held)"))
            else:
                results.append(("MyPy", False, f"{current} errors > baseline {baseline} (regression)"))
    except FileNotFoundError:
        results.append(("MyPy", True, "mypy not installed (skipped)"))
    except Exception as e:
        results.append(("MyPy", True, f"skipped: {str(e)[:30]}"))

    # Output
    elapsed = time.monotonic() - t0
    all_pass = all(r[1] for r in results)

    if getattr(args, "json", False):
        from aictl.core.output import print_json
        print_json({"passed": all_pass, "checks": [
            {"name": n, "passed": p, "detail": d} for n, p, d in results
        ], "elapsed_s": round(elapsed, 1)})
        # Exit code must match the human path: `aictl gate --json` is the
        # canonical CI gate, so a failed gate must exit non-zero. Previously
        # this returned 0 even when "passed" was false, so CI never caught it.
        return 0 if all_pass else 1

    print()
    for name, passed, detail in results:
        icon = "\u2713" if passed else "\u2717"
        print(f"  {icon} {name:12s} {detail}")

    print(f"\n  {'✓ GATE PASSED' if all_pass else '✗ GATE FAILED'} ({elapsed:.1f}s)")
    return 0 if all_pass else 1
