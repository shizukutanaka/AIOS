#!/usr/bin/env bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)
# gh_repo_health_pr.sh — L4 community-health PRs (repo scope only, NO workflow files).
# Adds .gitignore (if missing) and/or SECURITY.md (if missing), each as its own PR
# with a dedup guard (skips if an open PR of the same kind already exists).
# Usage: gh_repo_health_pr.sh <owner/repo> <gitignore|security|both>
set -u
REPO="${1:?owner/repo required}"; KIND="${2:?gitignore|security|both required}"
TOK=$(gh auth token)
BR_PREFIX="hygiene-health"
TMP="${TMPDIR:-/tmp}/hlth_$(echo "$REPO" | tr '/' '_')_$$"
rm -rf "$TMP"
git clone --depth 1 "https://x-access-token:${TOK}@github.com/$REPO.git" "$TMP" >/dev/null 2>&1 || { echo "  clone failed $REPO"; exit 1; }
cd "$TMP" || exit 1
git config user.email "ci-bot@local"; git config user.name "gh-inbox-hygiene"

mk_pr() {
  local kind="$1" file="$2"
  # dedup: skip if file already present, or an open PR of this kind exists
  if [ -f "$file" ]; then echo "  $REPO: $file 已存在 → skip"; return; fi
  if gh pr list --repo "$REPO" --state open --json title 2>/dev/null | grep -qi "$kind"; then
    echo "  $REPO: open $kind PR already exists → skip"; return; fi
  git checkout -b "${BR_PREFIX}-${kind}-$$" >/dev/null 2>&1
  case "$kind" in
    gitignore)
      cat > .gitignore <<'GI'
# OS / editor
.DS_Store
Thumbs.db
*.swp
*~
.idea/
.vscode/
# dependencies
node_modules/
venv/
__pycache__/
*.pyc
# env / secrets
.env
.env.local
*.secret
GI
      MSG="chore: add .gitignore to keep the repo clean (no committed junk/secrets)"
      BODY="Add a standard .gitignore (OS/editor cruft, dependency dirs, env/secrets). Keeps the repo clean and avoids accidentally committing secrets. Non-workflow file — repo scope only."
      ;;
    security)
      cat > SECURITY.md <<'SEC'
# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

- Open a private security advisory on this repository (Security → Report a vulnerability), **or**
- Email the maintainer via GitHub (do not open a public issue for vulnerabilities).

We will acknowledge reports within a few days and work with you on a fix and coordinated disclosure.
SEC
      MSG="docs: add SECURITY.md with a responsible-disclosure policy"
      BODY="Add a SECURITY.md documenting how to report vulnerabilities responsibly (private advisory / maintainer contact). Non-workflow file — repo scope only."
      ;;
  esac
  git add -u
  git commit -q -m "$MSG"
  local head; head="$(git rev-parse --abbrev-ref HEAD)"
  git -c "credential.helper=" push "https://x-access-token:${TOK}@github.com/$REPO.git" "$head" >/dev/null 2>&1
  local num; num=$(gh pr create --repo "$REPO" --head "$head" --title "$MSG" --body "$BODY" 2>/dev/null | grep -oE '[0-9]+$' | head -1)
  echo "  $REPO: opened PR #${num:-?} ($kind)"
}

case "$KIND" in
  gitignore) mk_pr gitignore .gitignore ;;
  security)  mk_pr security SECURITY.md ;;
  both)      mk_pr gitignore .gitignore; cd "$TMP" || exit 1; git checkout -q main 2>/dev/null || git checkout -q master 2>/dev/null; mk_pr security SECURITY.md ;;
esac
cd $FLEET_HOME && rm -rf "$TMP"
