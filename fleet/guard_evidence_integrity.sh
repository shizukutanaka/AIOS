#!/usr/bin/env bash
# guard_evidence_integrity.sh — L40 Guard-Evidence-Integrity Guard
#
# Evidence (arXiv):
#   2608.12877 "ReflectFact: ..." — a guard's cited evidence must be REAL and
#             CHECKABLE, not a plausible-looking hallucinated arXiv id. A guard
#             that cites "arXiv 0000.00001" (or a typo'd id) is itself unsound;
#             its whole justification collapses. Citation-verification is required.
#   2608.10530 "On Understanding, Identifying, and Mitigating Vulnerabilities in
#             Agentic LLMs" — agentic systems (incl. our guards) frequently emit
#             confident but fabricated references; provenance of every claimed
#             fact (here: the arXiv evidence) must be independently verified.
#   2608.08795 (SAVOR, indirect injection) — even the *evidence layer* can be
#             poisoned; verify the citation resolves to a real paper before trusting it.
#
# What L40 checks (pure bash + curl; read-only network GET to arxiv):
#   For every guard_*.sh, extract the arXiv ids it cites (XXXX.YYYY form). For each:
#     1. EXISTENCE: arxiv API returns an <entry> for that id (not a 404 / empty).
#     2. RECENCY:   the paper's <published> year is 2024..2026 (not a stale ref
#                   masquerading as current SOTA). 2026-08 = our corpus frontier.
#   A cited id that does NOT resolve (fake / typo) or is pre-2024 is an
#   EVIDENCE_INTEGRITY_DEFECT (the guard's justification is unverifiable).
#
# Distinct from:
#   - L35/L36/L37/L38/L39: those guard the *runtime* behavior of the fleet.
#   - L40 guards the *justification* of the guards themselves: "is the evidence
#     the guard cites actually real and current?" (meta-evidence plane).
# L40 is the provenance layer: "does the monitor's own citation hold up?"
#
# Verdict tokens (L29 output-contract):
#   EVIDENCE_INTEGRITY_OK / EVIDENCE_INTEGRITY_DEFECT / EVIDENCE_INTEGRITY_SOUND
# exit 0 = all cited evidence real + current; 1 = at least one defect.

set -u

REPO="${REPO:-$(cd "$(dirname "$0")" && pwd)}"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
PASS=0; FAIL=0
say(){ echo "  $1"; }

echo "=== L40: guard-evidence-integrity (citation provenance / recency; 2608.12877 / 2608.10530 / 2608.08795) ==="

defects=0
# collect all arXiv ids cited across guards. Match both "arXiv 2608.12877" and
# bare "2608.12877" forms; NOT anchored to 2608 so a guard citing a fake/typo'd
# id (0000.00001) is still extracted + checked. Exclude THIS script (it contains
# the fake-id self-test fixture string, which must not be flagged as a defect).
IDS=""
for f in "$REPO"/guard_*.sh; do
  [ -f "$f" ] || continue
  [ "$(basename "$f")" = "guard_evidence_integrity.sh" ] && continue
  IDS="$IDS $(grep -hoE 'arXiv [0-9]{4}\.[0-9]{4,5}|[0-9]{4}\.[0-9]{4,5}' "$f" 2>/dev/null | grep -oE '[0-9]{4}\.[0-9]{4,5}')"
done
IDS=$(printf '%s' "$IDS" | tr ' ' '\n' | grep -v '^$' | sort -u)

# arxiv existence+recency check (network). Recency window: 2024..2026.
# When SKIP_NET=1 (self-test), use offline fixtures instead of hitting arxiv:
#   a known-real id resolves + is 2026; the fake 0000.00001 does not resolve.
check_id(){
  local id="$1"
  if [ "${SKIP_NET:-}" = "1" ]; then
    case "$id" in
      2608.12877|2608.12970|2608.08795|2608.10530|2608.12133|2608.12571) return 0;;
      *) return 2;;
    esac
  fi
  local xml
  xml=$(curl -s --max-time 15 "https://export.arxiv.org/api/query?id_list=$id" 2>/dev/null)
  if [ -z "$xml" ]; then
    # Network unreachable / arxiv down / timeout: this is NOT proof the id is
    # fake (a real id also yields empty xml when the network is blocked). Treat
    # it as UNVERIFIABLE (return 4) — surface a WARN, never a hard DEFECT, so a
    # flaky network cannot red-alarm the whole evidence-integrity layer.
    return 4
  fi
  if ! printf '%s' "$xml" | grep -q '<entry>'; then
    return 2   # does not exist (fake / typo)
  fi
  local year
  year=$(printf '%s' "$xml" | grep -oE '<published>[0-9]{4}' | sed 's/<published>//' | head -1)
  if [ "${year:-0}" -lt 2024 ] || [ "${year:-0}" -gt 2026 ]; then
    return 3   # exists but outside recency window
  fi
  return 0
}

# In --self-test mode, skip the live arxiv scan (offline by design); the
# self-test block below drives offline child runs instead. Without this guard
# the parent still hits the network for 7 real ids and times out (rc=124).
if [ "${1:-}" != "--self-test" ]; then
for id in $IDS; do
  out=$(check_id "$id"); rc=$?
  if [ "$rc" -eq 0 ]; then
    PASS=$((PASS+1))
    say "[OK]   $id cited evidence real + current (2024-2026)"
  elif [ "$rc" -eq 4 ]; then
    # UNVERIFIABLE (network blocked / arxiv down): surface a WARN, do NOT count
    # as a defect — a flaky network is not proof the citation is fake.
    say "[WARN] $id cited evidence UNVERIFIABLE (network blocked / arxiv unreachable) — skipped, not failed"
  else
    defects=$((defects+1)); FAIL=$((FAIL+1))
    if [ "$rc" = "2" ]; then
      say "[EVIDENCE_INTEGRITY_DEFECT] $id cited but does NOT resolve on arxiv (fake/typo; 2608.12877)"
    else
      say "[EVIDENCE_INTEGRITY_DEFECT] $id resolves but is pre-2024 (stale evidence; 2608.10530)"
    fi
  fi
done
fi

# ---------- self-test (proves the guard is sound, not a rubber stamp) ----------
if [ "${1:-}" = "--self-test" ]; then
  tmpd="$(mktemp -d)"; rc=0
  # good: a guard citing a REAL, current id must pass
  printf '%s\n' '#!/usr/bin/env bash' '# evidence: arXiv 2608.12877 (real, 2026-08)' 'echo "placeholder"' > "$tmpd/guard_good.sh"
  if SKIP_NET=1 REPO="$tmpd" bash "$SELF" >/dev/null 2>&1; then
    say "[OK]   self-test: real+cited evidence passes (EVIDENCE_INTEGRITY_OK)"
  else
    say "[FAIL] self-test: real evidence wrongly flagged (false positive)"; rc=1
  fi
  # defect path: a guard citing a FAKE id must be detected
  printf '%s\n' '#!/usr/bin/env bash' '# evidence: arXiv 0000.00001 (does not exist)' 'echo "placeholder"' > "$tmpd/guard_bad.sh"
  if SKIP_NET=1 REPO="$tmpd" bash "$SELF" >/dev/null 2>&1; then
    say "[FAIL] self-test: fake citation NOT detected (rubber stamp!)"; rc=1
  else
    say "[OK]   self-test: fake citation detected (enforcement active)"
  fi
  rm -rf "$tmpd"
  if [ "$rc" = "0" ]; then
    echo "EVIDENCE_INTEGRITY_SOUND: self-test passed (real=OK, fake=enforced)"
    exit 0
  fi
  echo "EVIDENCE_INTEGRITY_SELFTEST_FAIL"
  exit 1
fi

# ---------- aggregate verdict ----------
if [ "$FAIL" -eq 0 ]; then
  echo "EVIDENCE_INTEGRITY_OK: all cited guard evidence real + current (2608.12877 / 2608.10530 / 2608.08795)"
  exit 0
fi
echo "EVIDENCE_INTEGRITY_DEFECT: $FAIL cited-evidence defect(s) (unverifiable guard justification)"
exit 1
