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

from aictl.core.output import ok, warn, err
from aictl.core.constants import AICTL_VERSION


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "update",
        help="Update aictl and model catalog.",
    )
    sp = p.add_subparsers(dest="update_cmd")
    sp.required = True

    sp.add_parser("check", help="Check for updates without applying.").set_defaults(func=run_check)
    sp.add_parser("models", help="Refresh the local model database.").set_defaults(func=run_models)
    upd = sp.add_parser("self", help="Update aictl to the latest version.")
    upd.add_argument("--dry-run", action="store_true",
                     help="Show what would happen without doing it.")
    upd.set_defaults(func=run_self)


def run_check(args: argparse.Namespace) -> int:
    """Check for updates."""
    print()
    print(f"  Current version: aictl {AICTL_VERSION}")
    print()

    # Try GitHub releases API (best-effort, no error if offline)
    latest = _fetch_latest_version()
    if latest is None:
        warn("Cannot reach GitHub. Check manually:")
        print("  https://github.com/shizukutanaka/aios/releases")
        return 0

    if latest == AICTL_VERSION:
        ok(f"Already up to date ({AICTL_VERSION})")
    else:
        warn(f"Update available: {AICTL_VERSION} → {latest}")
        print()
        print("  Run:  aictl update self")
    print()
    return 0


def run_models(args: argparse.Namespace) -> int:
    """Refresh model metadata from the repository's model list."""
    print()
    print("  Refreshing model database...")
    try:
        # Pull latest model info from GitHub raw content
        import urllib.request
        url = (
            "https://raw.githubusercontent.com/shizukutanaka/aios/"
            "main/aictl/runtime/recommend.py"
        )
        with urllib.request.urlopen(url, timeout=10) as r:
            content = r.read().decode("utf-8")
        # Count model entries as a quick sanity check
        count = content.count("ModelRec(")
        if count >= 20:
            ok(f"Model catalog is current ({count} models in upstream)")
        else:
            warn("Upstream model count seems low — check manually")
        print()
    except Exception as e:
        warn(f"Cannot reach upstream: {e}")
        print("  Model catalog not updated. Run when online.")
        print()
    return 0


def run_self(args: argparse.Namespace) -> int:
    """Update aictl via git pull or pip install -e."""
    dry = getattr(args, "dry_run", False)
    print()
    print(f"  Current: aictl {AICTL_VERSION}")

    # Detect how we were installed
    repo_root = _find_repo_root()
    if repo_root is None:
        # Pip install
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
               "git+https://github.com/shizukutanaka/aios.git"]
        print("  Method: pip upgrade")
    else:
        # Git repo
        cmd = ["git", "-C", str(repo_root), "pull", "--ff-only"]
        print(f"  Method: git pull in {repo_root}")

    print(f"  Command: {' '.join(cmd)}")

    if dry:
        print()
        print("  (dry-run — not executed)")
        return 0

    print()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            ok("Update complete. Restart aictl to use the new version.")
            if result.stdout.strip():
                print(f"  {result.stdout.strip()}")
        else:
            err("Update failed.")
            if result.stderr.strip():
                print(f"  {result.stderr.strip()}")
            return 1
    except subprocess.TimeoutExpired:
        err("Update timed out. Try again later.")
        return 1
    except Exception as e:
        err(f"Update error: {e}")
        return 1
    print()
    return 0


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
