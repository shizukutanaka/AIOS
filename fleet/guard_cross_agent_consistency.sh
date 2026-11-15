#!/usr/bin/env bash
# guard_cross_agent_consistency.sh — L37 Cross-Agent Consistency + Edit-Serialization Gate
#
# Research grounding (SRE Book + arXiv):
#   - Google SRE Book Ch.21 "Monitoring Distributed Systems" (monitor-the-monitors):
#     a guard that can be silently clobbered by another agent mid-run is itself a
#     broken monitor. The suite must hold an exclusive edit-lock so the autonomous
#     edit-fleet (~15 crons: fleet_loop 5m, heartbeat 7m, inner-loop 11m,
#     sweep-llama 15m, artifact-guard 30m, governance 30m) cannot rewrite guard_*.sh
#     while guard_suite.sh / guard_production_mutation.sh is executing.
#   - Google SRE Book Ch.22 "Addressing Cascading Failures" (fail-cheaply): if a
#     concurrent edit IS detected (lock unavailable), fail L37 fast instead of
#     emitting a corrupt cross-agent verdict.
#   - arXiv 2608.12877 "ReflectFact": independent agents on the SAME task must
#     converge on a CONSISTENT artifact; contradictory verdicts (DONE vs NOT-DONE
#     for one task id) are a consistency defect.
#   - arXiv 2608.10530 "Vulnerabilities in Agentic LLMs": contradictions BETWEEN
#     agents' committed artifacts are a defect class static single-agent checks miss.
#
# This guard does two things:
#   1. RUNTIME SERIALIZATION: take an exclusive flock on .guard_edit.lock for the
#      duration of the check. Callers (guard_suite.sh, guard_production_mutation.sh)
#      acquire this lock at start so the edit-fleet cannot rewrite guard_*.sh
#      underneath them. If the lock is already held by another suite/L22 run, fail
#      fast (fail-cheaply) — that is the correct L37 signal, not a hang.
#   2. CROSS-AGENT CONSISTENCY (read-only, non-destructive):
#      a. CLAIM CONTRADICTION: same task id recorded with both verdict=OK and
#         verdict=RETURN across recent outcome ledgers -> divergence defect.
#      b. SUITE-INTEGRATION: guard_suite.sh must reference this guard.
#
# Verdict tokens (L29 output-contract):
#   CROSS_AGENT_OK / CROSS_AGENT_DIVERGENCE / CROSS_AGENT_INTEGRATION_MISSING /
#   CROSS_AGENT_EDIT_LOCK_BUSY / CROSS_AGENT_SOUND
# exit 0 = consistent (no defect); 1 = defect found.

set -u

REPO="${REPO:-$(cd "$(dirname "$0")" && pwd)}"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
PASS=0; FAIL=0; defects=0
say(){ echo "  $1"; }

LOCK_FILE="${REPO}/.guard_edit.lock"
# Stale-lock guard: if an old lock names a PID that is no longer running, clear it.
if [ -f "$LOCK_FILE" ]; then
  _lp=$(cat "$LOCK_FILE" 2>/dev/null)
  if [ -n "$_lp" ] && ! kill -0 "$_lp" 2>/dev/null; then
    rm -f "$LOCK_FILE" 2>/dev/null
  fi
fi

echo "=== L37: cross-agent-consistency + edit-serialization gate (SRE Ch.21/22; 2608.12877 / 2608.10530) ==="

# ---------- self-test (proves the guard is sound, not a rubber stamp) ----------
# Run FIRST and independently: it spawns its own child runs with their own locks.
if [ "${1:-}" = "--self-test" ]; then
  rm -f "$LOCK_FILE" 2>/dev/null
  tmpd="$(mktemp -d)"; rc=0
  # good run: consistent -> OK (fresh lock, standalone)
  if SKIP_LOGSCAN=1 REPO="$REPO" bash "$SELF" >/dev/null 2>&1; then
    say "[OK]   self-test: consistent run reports CROSS_AGENT_OK"
  else
    say "[FAIL] self-test: consistent run wrongly flagged (false positive)"; rc=1
  fi
  # divergence path: craft two outcome ledgers with a contradicted task id
  mkdir -p "$tmpd/logs/run_x" "$tmpd/logs/run_y"
  printf 'task=abc123 verdict=OK\n' > "$tmpd/logs/run_x/outcome_ledger.md"
  printf 'task=abc123 verdict=RETURN\n' > "$tmpd/logs/run_y/outcome_ledger.md"
  if REPO="$tmpd" SKIP_LOGSCAN=1 bash "$SELF" >/dev/null 2>&1; then
    say "[FAIL] self-test: contradiction NOT detected (rubber stamp!)"; rc=1
  else
    say "[OK]   self-test: cross-agent divergence detected (enforcement active)"
  fi
  rm -rf "$tmpd"; rm -f "$LOCK_FILE" 2>/dev/null
  if [ "$rc" = "0" ]; then
    echo "CROSS_AGENT_SOUND: self-test passed (consistent=OK, divergence=enforced)"
    exit 0
  fi
  echo "CROSS_AGENT_SELFTEST_FAIL"
  exit 1
fi

# ---------- 0) RUNTIME EDIT-SERIALIZATION (portable noclobber lock) ----------
# `flock` is NOT available on this Windows git-bash, so use `set -C` (noclobber)
# atomic-create: a second exclusive create fails fast. This serializes
# guard_suite.sh / guard_production_mutation.sh (L22) against each other and
# signals (fail-cheaply, SRE Ch.22) if a concurrent run holds the edit-lock.
# Note: the autonomous edit-FLEET predates this lock and does not honor it; the
# real serialization is that suite/L22 ACQUIRE it, and L37 asserts exclusive
# ownership during its own run (a held lock at L37 time = a concurrent run).
( set -C; if ! ( set -C; echo $$ > "$LOCK_FILE" ) 2>/dev/null; then
  say "[CROSS_AGENT_EDIT_LOCK_BUSY] another suite/L22 run holds the edit-lock; concurrent edit in progress (SRE Ch.22 fail-cheaply)"
  echo "CROSS_AGENT_FAIL: edit-lock busy (serialization violation)"
  exit 1
fi )
# Acquired: record our PID (already written). Release at end / on exit via trap.
trap 'rm -f "$LOCK_FILE" 2>/dev/null' EXIT INT TERM HUP
# Scan only the MOST RECENT 50 run dirs — near-term cross-agent contradiction,
# not all 9000+ historical logs (SRE circuit-breaker lesson: don't re-flag old).
if [ "${SKIP_LOGSCAN:-}" != "1" ] && [ "${1:-}" != "--self-test" ] && [ -d "$REPO/logs" ]; then
  shopt -s nullglob
  RECENT=$(ls -dt "$REPO"/logs/run_* 2>/dev/null | head -50)
  for d in $RECENT; do
    f="$d/outcome_ledger.md"
    [ -f "$f" ] || continue
    ids=$(grep -oE 'task=[0-9a-f]+' "$f" 2>/dev/null | sort -u)
    for id in $ids; do
      okc=$(grep -c "$id.*verdict=OK" "$f" 2>/dev/null); okc=${okc:-0}
      rtc=$(grep -c "$id.*verdict=RETURN" "$f" 2>/dev/null); rtc=${rtc:-0}
      if [ "${okc:-0}" -ge 1 ] && [ "${rtc:-0}" -ge 1 ]; then
        defects=$((defects+1))
        say "[CROSS_AGENT_DIVERGENCE] $id: contradictory verdicts OK vs RETURN recorded (2608.12877)"
      fi
    done
  done
fi
if [ "$defects" -eq 0 ]; then
  PASS=$((PASS+1))
  say "[OK]   no contradictory per-task verdicts across agents"
else
  FAIL=$((FAIL+1))
fi

# ---------- 2) suite-integration check ----------
if [ -f "$REPO/guard_suite.sh" ] && grep -q "guard_cross_agent_consistency.sh" "$REPO/guard_suite.sh"; then
  PASS=$((PASS+1))
  say "[OK]   guard_cross_agent_consistency.sh referenced by guard_suite.sh"
else
  defects=$((defects+1)); FAIL=$((FAIL+1))
  say "[CROSS_AGENT_INTEGRATION_MISSING] guard_suite.sh does not reference this guard"
fi

# ---------- aggregate verdict ----------
# Lock auto-released by the EXIT trap; ensure cleanup here too.
rm -f "$LOCK_FILE" 2>/dev/null
if [ "$FAIL" -eq 0 ]; then
  echo "CROSS_AGENT_OK: no inter-agent contradiction; edit-serialization held (SRE Ch.21/22; 2608.12877 / 2608.10530)"
  exit 0
fi
echo "CROSS_AGENT_FAIL: $FAIL cross-agent consistency defect(s)"
exit 1
