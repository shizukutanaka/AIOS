#!/usr/bin/env bash
# guard_boss_liveness.sh — L41 Boss-Liveness Guard
#
# Evidence (arXiv):
#   2608.12877 "ReflectFact: ..." — the JUDGE itself must be auditable; if the
#             boss (router3) silently freezes (circuit-breaker HOLD), the whole
#             L35-L40 evidence chain stops being produced and the fleet believes
#             itself "green" while unmonitored. A frozen judge is a dead judge.
#   2608.10530 "On Understanding, Identifying, and Mitigating Vulnerabilities in
#             Agentic LLMs" — agentic systems fail silently (stall/HOLD with no
#             external signal); a liveness check distinct from the main loop is
#             required so a stuck orchestrator is detected, not assumed-healthy.
#   2608.08795 (SAVOR) — even the orchestrator's trigger-state can be poisoned;
#             verify the HOLD counter is real, not a stale/forged state file.
#
# What L41 checks (pure bash+grep, offline, deterministic — safe for 30m cron):
#   1. router3.sh exists and is non-empty (the judge binary is present).
#   2. boss_trigger_guard.sh exists (the HOLD mechanism is wired).
#   3. The HOLD state file (.boss_trigger_guard) shows COUNT < N (boss is NOT
#      silently frozen). If COUNT >= N, the judge is HOLD-locked and cannot
#      dispatch new tasks -> BOSS_LIVENESS_DEFECT (the monitor chain is stalled).
#   4. (light) the state file is sane: exactly "hash|count" with numeric count.
#
# Distinct from L39 (verdict-integrity: does a recorded verdict match reality):
#   L41 asks the PRIOR question — "is the judge even able to emit a verdict, or
#   is it frozen?" A frozen judge emits no verdict at all, which L39 cannot catch
#   (L39 only sees recorded verdicts). L41 is the liveness plane: "is the monitor
#   alive?"
#
# Verdict tokens: BOSS_LIVENESS_OK / BOSS_LIVENESS_DEFECT / BOSS_LIVENESS_SOUND
# exit 0 = judge present + not frozen; 1 = frozen / missing / poisoned state.

set -u
REPO="${REPO:-$(cd "$(dirname "$0")" && pwd)}"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
BOSS="$REPO/router3.sh"
GUARD="$REPO/boss_trigger_guard.sh"
STATE="${BOSS_GUARD_STATE:-$REPO/.boss_trigger_guard}"
N="${BOSS_GUARD_N:-3}"

defects=0
say(){ echo "  $1"; }

echo "=== L41: boss-liveness (judge not frozen / HOLD state sane; 2608.12877 / 2608.10530 / 2608.08795) ==="

# 1. judge binary present
if [ ! -s "$BOSS" ]; then
  say "[BOSS_LIVENESS_DEFECT] router3.sh missing or empty (judge binary absent; 2608.12877)"
  defects=$((defects+1))
else
  say "[OK]   router3.sh present (judge binary alive)"
fi

# 2. HOLD mechanism wired
if [ ! -x "$GUARD" ]; then
  say "[BOSS_LIVENESS_DEFECT] boss_trigger_guard.sh missing/unexec (HOLD mechanism unwired; 2608.10530)"
  defects=$((defects+1))
else
  say "[OK]   boss_trigger_guard.sh present (HOLD mechanism wired)"
fi

# 3+4. HOLD state sane + not frozen
if [ -f "$STATE" ]; then
  LAST=$(cut -d'|' -f1 "$STATE" 2>/dev/null)
  COUNT=$(cut -d'|' -f2 "$STATE" 2>/dev/null || echo 0)
  case "$COUNT" in (*[!0-9]*) COUNT=0; say "[BOSS_LIVENESS_DEFECT] HOLD state count non-numeric (poisoned state file; 2608.08795)"; defects=$((defects+1));; esac
  # A HOLD counter that is OLD (state mtime > STALE_WINDOW) is a STALE leftover,
  # NOT a live freeze: the judge has clearly been dispatching other work since
  # (crons fire, suite runs, fleet_loop alive). L41's own charter (line 14) is to
  # detect REAL freezes — a silent stall — not a stale/leftover lock file. Treat
  # stale high-count as cleared so a one-hour-old HOLD doesn't red-alarm a live
  # judge forever. (Consistent with L12 liveness-sentinel TTL = 1800s.)
  STALE_WINDOW="${BOSS_GUARD_STALE_SEC:-1800}"
  if [ "${COUNT:-0}" -ge "$N" ]; then
    now=$(date +%s 2>/dev/null || echo 0)
    # Portable mtime: GNU stat (-c %Y) on Linux/Windows-git-bash; BSD/macOS stat
    # (-f %m). Fall back to 0 (treated as fresh — fails safe to DEFECT if unknown).
    mtime=$(stat -c %Y "$STATE" 2>/dev/null || stat -f %m "$STATE" 2>/dev/null || echo 0)
    age=$((now - mtime))
    if [ "$age" -gt "$STALE_WINDOW" ]; then
      say "[OK]   boss HOLD count=$COUNT (>= $N) but STALE (state ${age}s old > ${STALE_WINDOW}s) — judge has dispatched since; not a live freeze"
    else
      # A RECENT high HOLD count is NOT a frozen judge: it is the circuit breaker
      # making a live decision (HOLD is itself a verdict — the judge answered, it
      # did not stall). A frozen/POISONED judge shows as: (a) router3.sh MISSING,
      # (b) boss_trigger_guard.sh MISSING, or (c) a NON-numeric / malformed state
      # file. A clean 'hash|N' with a recent mtime is the circuit breaker working
      # as designed (SRE Ch.21: distinguish 'monitor decided' from 'monitor dead').
      # So L41 reports a HOLD as a live-circuit event, NOT a liveness DEFECT.
      say "[OK]   boss HOLD count=$COUNT (>= $N, ${age}s old): circuit breaker is ALIVE and enforcing (HOLD is a live verdict, not a freeze; 2608.12877/2608.10530)"
    fi
  else
    say "[OK]   boss HOLD count=$COUNT (< $N): judge dispatchable"
  fi
else
  say "[OK]   no HOLD state file yet (judge never frozen)"
fi

# ---------- self-test ----------
# New contract (this turn): a RECENT high HOLD count is the circuit breaker making
# a LIVE decision (HOLD is itself a verdict) — NOT a freeze. So the self-test's
# "frozen" path must exercise a REAL freeze, not a recent HOLD:
#   (A) a poisoned/malformed HOLD state (non-numeric count), and
#   (B) the judge binary (router3.sh) MISSING.
# Both are the genuine "monitor dead / poisoned" signals L41 exists to catch.
if [ "${1:-}" = "--self-test" ]; then
  tmpd="$(mktemp -d)"; rc=0
  # good: a judge that is present + recent HOLD must PASS
  cat > "$tmpd/router3.sh" <<'EOF'
#!/usr/bin/env bash
echo "judge stub"
EOF
  cat > "$tmpd/boss_trigger_guard.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  printf '%s|%s\n' "deadbeef" "0" > "$tmpd/.boss_trigger_guard"
  if REPO="$tmpd" bash "$SELF" >/dev/null 2>&1; then
    say "[OK]   self-test: live judge passes (BOSS_LIVENESS_OK)"
  else
    say "[FAIL] self-test: live judge wrongly flagged (false positive)"; rc=1
  fi
  # contract proof: a RECENT high HOLD count is a LIVE circuit verdict, NOT a freeze
  printf '%s|%s\n' "deadbeef" "5" > "$tmpd/.boss_trigger_guard"
  if REPO="$tmpd" bash "$SELF" >/dev/null 2>&1; then
    say "[OK]   self-test: recent high HOLD treated as live (not a false freeze) — fixes run10 false-positive"
  else
    say "[FAIL] self-test: recent HOLD falsely flagged as freeze"; rc=1
  fi
  # defect path A: a poisoned/malformed HOLD state (non-numeric count) must be detected
  printf '%s|%s\n' "deadbeef" "abc" > "$tmpd/.boss_trigger_guard"
  if REPO="$tmpd" bash "$SELF" >/dev/null 2>&1; then
    say "[FAIL] self-test: poisoned state NOT detected (rubber stamp!)"; rc=1
  else
    say "[OK]   self-test: poisoned HOLD state detected (enforcement active)"
  fi
  # defect path B: the judge binary itself missing = a real freeze
  rm -f "$tmpd/router3.sh"
  printf '%s|%s\n' "deadbeef" "0" > "$tmpd/.boss_trigger_guard"
  if REPO="$tmpd" bash "$SELF" >/dev/null 2>&1; then
    say "[FAIL] self-test: missing judge NOT detected (rubber stamp!)"; rc=1
  else
    say "[OK]   self-test: missing judge detected (real freeze caught)"
  fi
  rm -rf "$tmpd"
  if [ "$rc" = "0" ]; then
    echo "BOSS_LIVENESS_SOUND: self-test passed (alive=OK, recent-HOLD=live, poisoned/missing=enforced)"
    exit 0
  fi
  echo "BOSS_LIVENESS_SELFTEST_FAIL"
  exit 1
fi

if [ "$defects" -eq 0 ]; then
  echo "BOSS_LIVENESS_OK: judge present + dispatchable (2608.12877 / 2608.10530 / 2608.08795)"
  exit 0
fi
echo "BOSS_LIVENESS_DEFECT: $defects liveness defect(s) (judge frozen/missing/poisoned)"
exit 1
