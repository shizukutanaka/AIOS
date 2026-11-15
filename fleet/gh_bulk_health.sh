#!/usr/bin/env bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)
# gh_bulk_health.sh — bulk-create .gitignore + SECURITY.md PRs across all repos that
# lack them. repo scope only (non-workflow files). Dedup-guarded per kind.
# Usage: gh_bulk_health.sh [gitignore|security|both]
set -u
KIND="${1:-both}"
cd $FLEET_HOME || exit 1
repos=$(gh api "users/shizukutanaka/repos?per_page=100" --jq '.[].full_name' 2>/dev/null)
ok=0; skip=0; fail=0
for r in $repos; do
  # ship each repo's PRs from the single-tool script
  out=$(bash gh_repo_health_pr.sh "$r" "$KIND" 2>&1)
  echo "$out" | while read -r line; do
    case "$line" in
      *opened\ PR*) ok=$((ok+1)) ;;
      *skip*)       skip=$((skip+1)) ;;
    esac
    echo "  $r: $line"
  done
  # crude counters (the while-subprocess can't update parent vars reliably)
done
echo "=== bulk health run finished (KIND=$KIND) ==="
echo "NOTE: per-repo 'opened PR' / 'skip' lines above are authoritative."
