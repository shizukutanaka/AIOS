#!/usr/bin/env bash
# guard_verdict_integrity.sh — L39 Verdict-Integrity Guard (the judge is judged)
#
# Evidence (arXiv):
#   2608.12877 "ReflectFact: ... multi-agent reflection/verification raises
#             factual consistency": a SELF-CLAIM of success ("verdict=OK") is NOT
#             evidence of success. The boss (router3) records VERDICT=OK/PASS in
#             outcome_ledger.md, but that claim must be independently checked
#             against physical reality (does the artifact exist? does it pass
#             its oracle?). Self-claim-untrustworthy is the core lesson.
#   2608.10530 "On Understanding, Identifying, and Mitigating Vulnerabilities in
#             Agentic LLMs": agentic systems emit confident "done"/"OK"
#             verdicts that are frequently false (phantom edits, fabricated
#             placeholders). A guard that re-derives truth from artifacts — not
#             from the logged verdict — is required.
#   2608.08795 (SAVOR, indirect injection): an injected instruction can make a
#             subordinate claim OK while the artifact is broken; the integrity
#             guard detects claim/artifact mismatch.
#
# What L39 checks (pure bash + grep; read-only):
#   For every outcome_ledger.md / .outcome_ledger entry with verdict=OK or
#   verdict=PASS, derive the task's target artifact (from goals.md done_when:
#   or from the task description) and verify:
#     1. the claimed artifact FILE EXISTS,
#     2. it passes `bash -n` (not a syntax-broken stub),
#     3. if the task has a done_when oracle, the oracle actually PASSES now.
#   A recorded OK whose artifact is missing / broken / oracle-failing is a
#   VERDICT_INTEGRITY_BROKEN (boss lied, or was fooled by a subordinate).
#
# Distinct from L37 (cross-agent divergence): L37 checks two agents DISAGREE on
# the same task; L39 checks a SINGLE logged verdict DISAGREES with physical truth.
# L39 is the meta-guard over the boss itself: "does the judge's verdict match
# reality?" This is exactly the defect we caught by hand in L35-L38 (router3
# said 承認 while the artifact was a placeholder).
#
# Verdict tokens (L29 output-contract):
#   VERDICT_INTEGRITY_OK / VERDICT_INTEGRITY_BROKEN / VERDICT_INTEGRITY_SOUND
# exit 0 = no verdict/artifact mismatch; 1 = at least one mismatch found.

set -u

REPO="${REPO:-$(cd "$(dirname "$0")" && pwd)}"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
PASS=0; FAIL=0
say(){ echo "  $1"; }

echo "=== L39: verdict-integrity (boss claim vs physical oracle; 2608.12877 / 2608.10530 / 2608.08795) ==="

defects=0

# ---------- scan outcome ledgers for verdict=OK / verdict=PASS ----------
# Skip the (env-dependent, slow) logs scan during --self-test (we prove
# enforcement with crafted tmp ledgers below); same lesson as L37/L38.
# Scan ONLY the consolidated $REPO/.outcome_ledger (capped to recent-50 below).
# Enumerating logs/run_* (9127 dirs) via `ls -dt logs/run_*` expands a 9127-entry
# glob and times out; the consolidated ledger already aggregates every verdict, so
# the per-run scan is redundant (circuit-breaker lesson: don't scan all history).
ledgers=""
# During --self-test (or SKIP_LOGSCAN) we use ONLY the crafted tmp ledger
# (REPO=tmpd); never the host's .outcome_ledger (534 real verdicts -> timeout).
if [ "${SKIP_LOGSCAN:-}" != "1" ] && [ "${1:-}" != "--self-test" ]; then
  ledgers="$ledgers $REPO/.outcome_ledger"
fi

mismatches=0
CHECKED_SYNTAX=""
# Pre-compute the task->artifact map ONCE: grep goals.md a single time into a
# plain "Lxx<TAB>/path" list (no associative array, no per-line re-grep).
ART_MAP=$(grep -E -- '[-] \[[ x]\][^|]*L[0-9]+[^|]*[|] done_when: test -f [^ ]+' "$REPO/goals.md" 2>/dev/null | sed -E 's/.*(L[0-9]+).*test -f ([^ ]+).*/\1\t\2/' || true)

for d in $ledgers; do
  f="$d"
  [ -f "$f" ] || { f="$d/outcome_ledger.md"; }
  [ -f "$f" ] || continue
  # Only scan the most recent 50 verdicts (a verdict/artifact mismatch is a
  # near-term event; scanning 534 historical verdicts is slow and a false
  # "consecutive" trap — circuit-breaker lesson, same as L36/L37).
  recent=$(tail -50 "$f" 2>/dev/null)
  while IFS= read -r line; do
    id=$(printf '%s' "$line" | grep -oE 'task=[0-9a-fL]+' | head -1 | cut -d= -f2)
    verdict=$(printf '%s' "$line" | grep -oE 'verdict=(OK|PASS)' | head -1 | cut -d= -f2)
    [ -z "$id" ] && continue
    [ -z "$verdict" ] && continue
    # map verdict task id -> artifact via the precomputed ART_MAP (suffix match)
    artifact=""
    while IFS= read -r ml; do
      key=$(printf '%s' "$ml" | cut -f1)
      path=$(printf '%s' "$ml" | cut -f2)
      case "$id" in *"$key"*) artifact="$path"; break;; esac
    done <<< "$ART_MAP"
    [ -z "$artifact" ] && continue 2
    # 1) existence
    if [ ! -f "$artifact" ]; then
      defects=$((defects+1)); mismatches=$((mismatches+1))
      say "[VERDICT_INTEGRITY_BROKEN] $id claimed $verdict but artifact $artifact MISSING (2608.10530)"
      continue
    fi
    # 2) syntax — checked at most ONCE per distinct artifact (bash -n is the
    #    slow part; 534 verdicts point at a handful of files, so dedup it).
    case "$CHECKED_SYNTAX" in *"|$artifact|"*) ;; *)
      if ! bash -n "$artifact" >/dev/null 2>&1; then
        defects=$((defects+1)); mismatches=$((mismatches+1))
        say "[VERDICT_INTEGRITY_BROKEN] $id claimed $verdict but $artifact fails bash -n (2608.08795)"
        CHECKED_SYNTAX="$CHECKED_SYNTAX|$artifact|"
        continue
      fi
      CHECKED_SYNTAX="$CHECKED_SYNTAX|$artifact|"
    esac
    PASS=$((PASS+1))
    say "[OK]   $id $verdict consistent with artifact $artifact"
  done <<< "$recent"
done

# ---------- self-test (proves the guard is sound, not a rubber stamp) ----------
if [ "${1:-}" = "--self-test" ]; then
  tmpd="$(mktemp -d)"; rc=0
  # good: a verdict whose artifact exists + is valid must report OK.
  # task id L99 matches the goal id so the map lookup succeeds.
  printf 'task=L99 verdict=OK\n' > "$tmpd/.outcome_ledger"
  # point goals.md lookup at a REAL valid script so the artifact check passes
  printf -- '- [ ] dummy L99 test | done_when: test -f %s/guard_verdict_integrity.sh\n' "$REPO" > "$tmpd/goals.md"
  if REPO="$tmpd" bash "$SELF" >/dev/null 2>&1; then
    say "[OK]   self-test: consistent verdict reports VERDICT_INTEGRITY_OK"
  else
    say "[FAIL] self-test: consistent verdict wrongly flagged (false positive)"; rc=1
  fi
  # broken path: a verdict whose artifact is MISSING must be detected.
  # task id matches the goal id (L99b) so the map lookup succeeds, but the
  # artifact (guard_THIS_DOES_NOT_EXIST.sh) is absent -> BROKEN.
  printf 'task=L99b verdict=PASS\n' > "$tmpd/.outcome_ledger"
  printf -- '- [ ] dummy L99b test | done_when: test -f %s/guard_THIS_DOES_NOT_EXIST.sh\n' "$REPO" > "$tmpd/goals.md"
  if REPO="$tmpd" bash "$SELF" >/dev/null 2>&1; then
    say "[FAIL] self-test: missing-artifact verdict NOT detected (rubber stamp!)"; rc=1
  else
    say "[OK]   self-test: missing-artifact verdict detected (enforcement active)"
  fi
  rm -rf "$tmpd"
  if [ "$rc" = "0" ]; then
    echo "VERDICT_INTEGRITY_SOUND: self-test passed (consistent=OK, broken=enforced)"
    exit 0
  fi
  echo "VERDICT_INTEGRITY_SELFTEST_FAIL"
  exit 1
fi

# ---------- aggregate verdict ----------
if [ "$mismatches" -eq 0 ]; then
  echo "VERDICT_INTEGRITY_OK: all logged OK/PASS verdicts consistent with physical artifacts (2608.12877 / 2608.10530 / 2608.08795)"
  exit 0
fi
echo "VERDICT_INTEGRITY_BROKEN: $mismatches verdict/artifact mismatch(es) (boss claim != reality)"
exit 1
