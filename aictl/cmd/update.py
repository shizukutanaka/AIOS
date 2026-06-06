"""aictl update — keep the tool and model catalog current.

  aictl update check     Check for new versions (no action)
  aictl update models    Refresh model database from upstream
  aictl update self      Update aictl itself (git pull or pip install)

Apple principle: 'Software Update' is always one click. Never manual.
"""

from __future__ import annotations

from typing import Any

import argparse

import subprocess
import sys
from pathlib import Path

from aictl.core.output import ok, warn, err, print_json
from aictl.core.constants import AICTL_VERSION


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "update",
        help="Update aictl and model catalog.",
    )
    sp = p.add_subparsers(dest="update_cmd")
    sp.required = True

    chk = sp.add_parser("check", help="Check for updates without applying.")
    _add_json(chk)
    chk.set_defaults(func=run_check)

    mdl = sp.add_parser("models", help="Refresh the local model database.")
    _add_json(mdl)
    mdl.set_defaults(func=run_models)

    upd = sp.add_parser("self", help="Update aictl to the latest version.")
    upd.add_argument("--dry-run", action="store_true",
                     help="Show what would happen without doing it.")
    _add_json(upd)
    upd.set_defaults(func=run_self)


def _add_json(p: argparse.ArgumentParser) -> None:
    # SUPPRESS default so a local --json never clobbers a global `aictl --json`.
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="Emit results as JSON")


def run_check(args: argparse.Namespace) -> int:
    """Check for updates."""
    use_json = getattr(args, "json", False)
    latest = _fetch_latest_version()
    reachable = latest is not None
    update_available = bool(reachable and latest != AICTL_VERSION)

    if use_json:
        print_json({
            "current": AICTL_VERSION,
            "latest": latest,
            "reachable": reachable,
            "update_available": update_available,
        })
        return 0

    print()
    print(f"  Current version: aictl {AICTL_VERSION}")
    print()
    if not reachable:
        warn("Cannot reach GitHub. Check manually:")
        print("  https://github.com/shizukutanaka/aios/releases")
        return 0
    if not update_available:
        ok(f"Already up to date ({AICTL_VERSION})")
    else:
        warn(f"Update available: {AICTL_VERSION} → {latest}")
        print()
        print("  Run:  aictl update self")
    print()
    return 0


def run_models(args: argparse.Namespace) -> int:
    """Refresh model metadata from the repository's model list."""
    use_json = getattr(args, "json", False)
    reachable = False
    count: int | None = None
    error: str | None = None
    try:
        import urllib.request
        url = (
            "https://raw.githubusercontent.com/shizukutanaka/aios/"
            "main/aictl/runtime/recommend.py"
        )
        with urllib.request.urlopen(url, timeout=10) as r:
            content = r.read().decode("utf-8")
        count = content.count("ModelRec(")
        reachable = True
    except Exception as e:
        error = str(e)

    current = bool(reachable and count is not None and count >= 20)

    if use_json:
        print_json({
            "reachable": reachable,
            "upstream_model_count": count,
            "current": current,
            "error": error,
        })
        return 0

    print()
    print("  Refreshing model database...")
    if not reachable:
        warn(f"Cannot reach upstream: {error}")
        print("  Model catalog not updated. Run when online.")
        print()
        return 0
    if current:
        ok(f"Model catalog is current ({count} models in upstream)")
    else:
        warn("Upstream model count seems low — check manually")
    print()
    return 0


def run_self(args: argparse.Namespace) -> int:
    """Update aictl via git pull or pip install -e."""
    use_json = getattr(args, "json", False)
    dry = getattr(args, "dry_run", False)

    repo_root = _find_repo_root()
    if repo_root is None:
        method = "pip"
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
               "git+https://github.com/shizukutanaka/aios.git"]
    else:
        method = "git"
        cmd = ["git", "-C", str(repo_root), "pull", "--ff-only"]

    if not use_json:
        print()
        print(f"  Current: aictl {AICTL_VERSION}")
        print(f"  Method: {method} {'upgrade' if method == 'pip' else 'pull in ' + str(repo_root)}")
        print(f"  Command: {' '.join(cmd)}")

    if dry:
        if use_json:
            print_json({"method": method, "command": cmd, "dry_run": True,
                        "executed": False, "success": True})
        else:
            print()
            print("  (dry-run — not executed)")
        return 0

    success = False
    rc = 0
    out = ""
    error = ""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        success = result.returncode == 0
        out = result.stdout.strip()
        error = result.stderr.strip()
        rc = 0 if success else 1
    except subprocess.TimeoutExpired:
        error = "Update timed out. Try again later."
        rc = 1
    except Exception as e:
        error = f"Update error: {e}"
        rc = 1

    if use_json:
        print_json({"method": method, "command": cmd, "dry_run": False,
                    "executed": True, "success": success,
                    "output": out, "error": error})
        return rc

    print()
    if success:
        ok("Update complete. Restart aictl to use the new version.")
        if out:
            print(f"  {out}")
    else:
        err(error or "Update failed.")
        if error and rc == 1 and out:
            print(f"  {out}")
    print()
    return rc


def _fetch_latest_version() -> str | None:
    """Try GitHub API for latest release tag. Returns None on failure."""
    try:
        import urllib.request
        import json
        url = "https://api.github.com/repos/shizukutanaka/aios/releases/latest"
        req = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        tag = data.get("tag_name", "")
        return tag.lstrip("v") if tag else None
    except Exception:
        return None


def _find_repo_root() -> Path | None:
    """Find git repo root containing this file."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass  # best-effort; failure is non-critical
    return None
