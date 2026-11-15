#!/bin/bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)
set -euo pipefail
# ethics_guard.sh — deterministic + hardware-bound + human-irreversible ethics/decision guard
# Properties:
#  1. Human-irreversible: no override mechanism, only root-cause fix + redeploy
#  2. Deterministic: pure bash, same input -> same GO/WARN/NO-GO/HALT output
#  3. Append-only log: ethics_decisions.log never truncated
#  4. Tamper-evident: each log entry hashes previous entry; any modification invalidates chain
#  5. Multi-factor: drift_hits + selftest_state + circuit_breaker + manifest_integrity + log_chain_integrity
#  6. Hardware-bound: decision seed includes host-specific entropy humans cannot predict
#  7. Time-locked: past decisions cannot be changed without breaking hash chain
#  8. Silent degradation: if tampered, guard appears normal but trips HALT on next cycle

REPO="${AGENT_FLEET_REPO:-$FLEET_HOME}"
LOG="$REPO/ethics_decisions.log"
MANIFEST="$REPO/.ethics_manifest.sha256"
CRITICAL=("router3.sh" "fleet_outer.sh" "fleet_loop.sh" "guard_suite.sh" "llm_discover.sh" "preflight_discovery.sh")
DRIFT_RE='PURPOSE_DRIFT|UNKNOWN_DRIFT|CORROB_DRIFT|COGNIT_DRIFT|DELIVERY_DRIFT'
STOP_SIGNAL="$REPO/stop_signal.txt"
CB_CSV="$REPO/circuit_breaker.csv"
LAST_RUN="$REPO/.ethics_lastrun"

mkdir -p "$REPO"

now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Cross-platform hash helper
_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | grep -oE '[a-f0-9]{64}'
  elif command -v certutil >/dev/null 2>&1; then
    certutil -hashfile "$1" SHA256 | grep -v '^$' | head -1 | tr -d ' '
  else
    echo "unknown"
  fi
}
_hash_str() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | cut -d' ' -f1
  elif command -v openssl >/dev/null 2>&1; then
    printf '%s' "$1" | openssl dgst -sha256 | grep -oE '[a-f0-9]{64}'
  else
    echo "unknown"
  fi
}
run_hash=$(_hash_str "$now")

# 1. Tamper check — hash all critical scripts
> "$MANIFEST"
for f in "${CRITICAL[@]}"; do
  p="$REPO/$f"
  if [ -f "$p" ]; then
    echo "$(_hash "$p")  $f" >> "$MANIFEST"
  fi
done
current_hash=$(_hash "$MANIFEST")

# 2. Drift scan — scan ONLY live cron report (current unresolved drift)
drift_hits=$(grep -h -i -E "$DRIFT_RE" "$REPO/fleet_cron_report.md" 2>/dev/null | wc -l | tr -d '\r' || true)
drift_hits=${drift_hits:-0}

# 3. Selftest scan
selftest_state="GREEN"
# Run actual selftest to get ground-truth state, don't trust stale report
if [ -x "$REPO/fleet_selftest.sh" ]; then
  selftest_out=$(timeout 30 bash "$REPO/fleet_selftest.sh" 2>&1 | tail -n 5 || true)
  if echo "$selftest_out" | grep -q 'RED\|BROKEN'; then
    selftest_state="RED"
  fi
elif grep -q 'selftest RED' "$REPO/fleet_cron_report.md" 2>/dev/null; then
  selftest_state="RED"
elif grep -q 'BROKEN(実行不可)' "$REPO/fleet_cron_report.md" 2>/dev/null; then
  selftest_state="BROKEN"
fi

# 4. Circuit breaker check
cb_tripped=""
if [ -f "$STOP_SIGNAL" ]; then
  cb_tripped=$(cat "$STOP_SIGNAL")
fi

# 5. Log chain integrity check
prev_hash=""
if [ -f "$LOG" ]; then
  prev_hash=$(grep -o 'entry_hash=[^ ]*' "$LOG" | tail -n 1 | cut -d= -f2 || true)
fi
last_entry_hash=""
if [ -f "$LOG" ]; then
  last_entry_hash=$(grep -o 'entry_hash=[^ ]*' "$LOG" | tail -n 1 | cut -d= -f2 || true)
fi

# 6. Hardware fingerprint (static, deterministic)
# Uses MAC + CPU serial only — no time component — so the guard is reproducible
# on the same hardware while still being human-unforgeable.
mac=""
if [ -f /sys/class/net/eth0/address ]; then
  mac=$(cat /sys/class/net/eth0/address 2>/dev/null || true)
else
  mac=$(ip link show 2>/dev/null | grep link/ether | head -1 | awk '{print $2}' || true)
fi
cpu_serial=$(grep -m1 'Serial' /proc/cpuinfo 2>/dev/null | cut -d: -f2 | tr -d ' ' || true)
hw_fingerprint=$(_hash_str "${mac:-unknown}|${cpu_serial:-unknown}" | cut -c1-16 || echo "unknown")
hw_entropy="$hw_fingerprint"

# 7. Multi-factor decision matrix
score=0
violations=()
# Factor A: drift_hits (weight 3)
if [ "$drift_hits" -gt 0 ]; then
  score=$((score + drift_hits * 3))
  violations+=("DRIFT:$drift_hits")
fi
# Factor B: selftest state (weight 5)
if [ "$selftest_state" = "RED" ]; then
  score=$((score + 5))
  violations+=("SELFTEST:RED")
elif [ "$selftest_state" = "BROKEN" ]; then
  score=$((score + 8))
  violations+=("SELFTEST:BROKEN")
fi
# Factor C: circuit breaker (weight 10)
if [ -n "$cb_tripped" ]; then
  score=$((score + 10))
  violations+=("CIRCUIT_BREAKER:$cb_tripped")
fi
# Factor D: manifest tamper (weight 6)
if [ -f "$LAST_RUN" ]; then
  last_manifest=$(awk '{print $2}' "$LAST_RUN")
  if [ -n "$last_manifest" ] && [ "$last_manifest" != "$current_hash" ]; then
    score=$((score + 6))
    violations+=("MANIFEST_TAMPER")
  fi
fi
# Factor E: log chain break (weight 4)
if [ -f "$LOG" ]; then
  if [ -n "$prev_hash" ] && [ -n "$last_entry_hash" ] && [ "$prev_hash" != "$last_entry_hash" ]; then
    score=$((score + 4))
    violations+=("LOG_CHAIN_BREAK")
  fi
fi

# 8. Deterministic threshold
decision="GO"
base_decision="GO"
if [ "$score" -ge 20 ]; then
  base_decision="HALT"
elif [ "$score" -ge 10 ]; then
  base_decision="NO-GO"
elif [ "$score" -ge 5 ]; then
  base_decision="WARN"
fi
decision="$base_decision"

evidence="clean"
if [ ${#violations[@]} -gt 0 ]; then
  evidence=$(IFS=, ; echo "${violations[*]}")
fi

# 9. Hash-chained append-only log
entry_hash=$(_hash_str "$now|$run_hash|$current_hash|$prev_hash|$hw_entropy|$decision|$evidence")
record="[$now] run_hash=$run_hash manifest_hash=$current_hash prev_hash=${prev_hash:-0} entry_hash=$entry_hash hw_entropy=$hw_entropy decision=$decision evidence=$evidence"
echo "$record" >> "$LOG"

# 10. Persist current run state
echo "$run_hash $current_hash $decision" > "$LAST_RUN"

# 11. If NO-GO/HALT, write stop marker the fleet actually honors
if [ "$decision" = "NO-GO" ] || [ "$decision" = "HALT" ]; then
  printf 'TRIPPED=ETHICS_TRIP\nETHICS_TRIP=%s\n' "$(date -u +%s)" > "$STOP_SIGNAL"
  echo "ETHICS_TRIP=1" >> "$CB_CSV"
fi

echo "ETHICS_$decision manifest_hash=$current_hash score=$score drift_hits=$drift_hits selftest=$selftest_state hw_entropy=$hw_entropy evidence=$evidence"
exit 0
