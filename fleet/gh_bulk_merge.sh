#!/usr/bin/env bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)
# gh_bulk_merge.sh — merge ALL open PRs across the user's repos (user-approved:
# "全134 PR を一括マージ"). Continues past failures. Reports counts.
set -u
cd $FLEET_HOME || exit 1
repos=$(gh api "users/shizukutanaka/repos?per_page=100" --jq '.[].full_name' 2>/dev/null)
merged=0; failed=0; skipped=0
for r in $repos; do
  nums=$(gh pr list --repo "$r" --state open --json number,headRefName 2>/dev/null | python -c "
import sys,json
for p in json.load(sys.stdin):
    print(p['number'])
" 2>/dev/null | tr -d '\r')
  [ -z "$nums" ] && continue
  for n in $nums; do
    res=$(gh pr merge "$n" --repo "$r" --merge 2>&1)
  rc=$?
  if echo "$res" | grep -qiE 'merged|already merged'; then
    merged=$((merged+1)); echo "  MERGED $r#$n"
  elif echo "$res" | grep -qiE 'workflow.{0,12}scope'; then
    wfscope=$((wfscope+1)); echo "  WFSCOPE $r#$n (needs gh auth refresh -s workflow or web UI)"
  elif echo "$res" | grep -qiE 'not mergeable|conflict'; then
    conflict=$((conflict+1)); echo "  CONFLICT $r#$n (manual rebase needed)"
  elif [ $rc -ne 0 ]; then
    failed=$((failed+1)); echo "  FAIL($rc) $r#$n: $(echo "$res" | head -1)"
  else
    merged=$((merged+1)); echo "  MERGED(ok) $r#$n"
  fi
  done
done
echo "=== SUMMARY: merged=$merged wf_scope=$wfscope conflict=$conflict failed=$failed ==="
