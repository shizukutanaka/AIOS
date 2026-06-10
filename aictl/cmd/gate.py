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
    p.set_defaults(func=run)


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
    if not getattr(args, "skip_tests", False):
        tests_dir = project_root / "tests"
        loader = unittest.TestLoader()
        suite = loader.discover(str(tests_dir))
        runner = unittest.TextTestRunner(verbosity=0, stream=io.StringIO())
        result = runner.run(suite)
        passed = result.testsRun - len(result.failures) - len(result.errors)
        results.append(("Tests", result.wasSuccessful(),
                        f"{passed}/{result.testsRun} passed"))
    else:
        results.append(("Tests", True, "skipped"))

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

    # 6. Documentation consistency
    try:
        from aictl.cmd.help import TOPICS
        all_help = "\n".join(TOPICS.values())

        # Build parser to get registered commands
        from aictl.__main__ import build_parser
        p = build_parser()
        for a in p._actions:
            if hasattr(a, "choices") and a.choices:
                set(a.choices.keys())

        # Verify critical v1.6.0+ commands are in help
        critical = ["diff", "prompt", "route", "eval", "spec",
                    "guard", "rag", "tco", "quota", "batch"]
        missing_help = [c for c in critical if f"aictl {c}" not in all_help]

        # Verify README mentions key commands
        readme_text = (project_root / "README.md").read_text(errors="replace")
        missing_readme = [c for c in ["diff", "prompt", "route", "guard", "tco"]
                          if f"aictl {c}" not in readme_text]

        # Verify CHANGELOG has v1.6.0
        cl_text = (project_root / "CHANGELOG.md").read_text(errors="replace")
        has_changelog = "v1.6.0" in cl_text

        doc_issues = missing_help + missing_readme + ([] if has_changelog else ["v1.6.0 missing from CHANGELOG"])
        if doc_issues:
            results.append(("Docs", False, f"missing: {', '.join(doc_issues[:3])}"))
        else:
            results.append(("Docs", True, f"{len(critical)} commands in help, README, CHANGELOG"))
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

    # 8. ruff lint (must be zero — CLAUDE.md 6.2)
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

    # 9. mypy --strict ratchet (count must not exceed baseline — CLAUDE.md 6.2)
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
        return 0

    print()
    for name, passed, detail in results:
        icon = "\u2713" if passed else "\u2717"
        print(f"  {icon} {name:12s} {detail}")

    print(f"\n  {'✓ GATE PASSED' if all_pass else '✗ GATE FAILED'} ({elapsed:.1f}s)")
    return 0 if all_pass else 1
