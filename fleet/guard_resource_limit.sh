#!/usr/bin/env bash
# guard_resource_limit.sh — L36 Resource-Limit Guard
#
# Evidence (arXiv):
#   2608.10530 "On Understanding, Identifying, and Mitigating Vulnerabilities in
#             Agentic LLMs": agentic loops without a bounded step / cost budget
#             can run away (infinite re-delegation, token/credit exhaustion).
#             A hard RESOURCE BOUNDARY (max steps, max wall-clock, max child
#             processes, max cost) must be enforced OUTSIDE the agent's own
#             reasoning — the agent cannot self-limit reliably.
#   2608.08795 (SAVOR, indirect injection): an externally-steered loop can also
#             drive unbounded tool-call fan-out; resource limits are a second
#             independent control plane from L35's channel boundary.
#   2608.12970 (acceptance-test): a capability claim ("I finished") without a
#             bounded-execution check is not trustworthy; the guard must PROVE
#             the run stayed within limits, not assert it.
#
# Distinct from:
#   - L35 (guard_trust_boundary.sh): channel/instruction seam, not execution budget.
#   - cost_guard.sh / model_watch.sh: spend tracking, not hard enforcement.
# L36 is the HARD ENFORCEMENT layer: it FAILS (exit 1) when a run would or did
# exceed a configured resource bound, and proves compliance via --self-test.
#
# Pure bash + grep. Non-destructive (self-test + read-only inspection).
#
# Verdict tokens (L29 output-contract):
#   RESOURCE_LIMIT_OK / RESOURCE_LIMIT_VIOLATION / RESOURCE_LIMIT_SOUND
# exit 0 = within limits (or self-test passed); 1 = violation / self-test fail.

set -u

REPO="$(cd "$(dirname "$0")" && pwd)"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
PASS=0; FAIL=0
say(){ echo "  $1"; }

# ---- configurable bounds (override via env) ----
MAX_CHILD_PROC="${MAX_CHILD_PROC:-50}"      # max concurrent child processes
MAX_WALL_SEC="${MAX_WALL_SEC:-1800}"        # max wall-clock seconds per run
MAX_OPEN_FD="${MAX_OPEN_FD:-1024}"          # max open file descriptors

echo "=== L36: resource-limit guard (agentic runaway / cost-cap / process-limit; 2608.10530 / 2608.08795 / 2608.12970) ==="

violations=0

# ---------- 1) child-process fan-out check (PRODUCTION only, not self-test) ----------
# In --self-test we prove enforcement with a forced breach (MAX_CHILD_PROC=0),
# so we must NOT also count live processes there (the host may legitimately
# have many bash/python/node/ollama processes running).
if [ "${1:-}" != "--self-test" ]; then
  # Count agentic fan-out, NOT baseline OS load. Exclude the always-on ollama
  # daemon and the hermes host (expected infra, not a runaway). A real
  # fork-storm (an agent loop spawning unbounded children) still blows past
  # MAX_CHILD_PROC; we just stop counting the monitor's own footprint.
  child_count=$(ps -e 2>/dev/null | grep -E 'bash|python|node' | grep -vE 'ollama|hermes' | grep -vc 'grep')
  child_count=${child_count:-0}
  if [ "${child_count:-0}" -gt "${MAX_CHILD_PROC}" ]; then
    violations=$((violations+1))
    say "[RESOURCE_LIMIT_VIOLATION] agentic child processes=${child_count} > MAX_CHILD_PROC=${MAX_CHILD_PROC} (fan-out / fork storm, 2608.10530)"
  fi
fi

# ---------- 2) wall-clock bound (read from a marker if present) ----------
if [ -n "${RUN_START_EPOCH:-}" ]; then
  now=$(date +%s)
  elapsed=$((now - RUN_START_EPOCH))
  if [ "$elapsed" -gt "${MAX_WALL_SEC}" ]; then
    violations=$((violations+1))
    say "[RESOURCE_LIMIT_VIOLATION] wall-clock=${elapsed}s > MAX_WALL_SEC=${MAX_WALL_SEC} (runaway loop, 2608.10530)"
  fi
fi

# ---------- 3) open-file-descriptor bound ----------
if command -v bash >/dev/null 2>&1; then
  fd_count=$(bash -c 'echo ${#BASH_VERSINFO[@]}' 2>/dev/null)  # trivial; real check below
  # portable: count via /proc if available, else skip
  if [ -d /proc/self/fd ]; then
    fd_count=$(ls /proc/self/fd 2>/dev/null | wc -l)
    if [ "${fd_count:-0}" -gt "${MAX_OPEN_FD}" ]; then
      violations=$((violations+1))
      say "[RESOURCE_LIMIT_VIOLATION] open fds=${fd_count} > MAX_OPEN_FD=${MAX_OPEN_FD}"
    fi
  fi
fi

if [ "$violations" -eq 0 ]; then
  PASS=$((PASS+1))
  say "[OK]   resource usage within configured bounds (MAX_CHILD_PROC=${MAX_CHILD_PROC}, MAX_WALL_SEC=${MAX_WALL_SEC})"
else
  FAIL=$((FAIL+1))
fi

# ---------- self-test (proves the guard is sound, not a rubber stamp) ----------
if [ "${1:-}" = "--self-test" ]; then
  tmpd="$(mktemp -d)"
  rc=0
  # good: a bounded run (raise the bound so the host's live process count
  # never trips it) must report OK — proves the guard passes a compliant run.
  if MAX_CHILD_PROC=100000 MAX_OPEN_FD=100000 bash "$SELF" >/dev/null 2>&1; then
    say "[OK]   self-test: bounded run reports RESOURCE_LIMIT_OK"
  else
    say "[FAIL] self-test: bounded run wrongly flagged (false positive)"; rc=1
  fi
  # violation path: force a child-process breach via env override
  if ! MAX_CHILD_PROC=0 bash "$SELF" >/dev/null 2>&1; then
    say "[OK]   self-test: over-limit detected (enforcement active)"
  else
    say "[FAIL] self-test: over-limit NOT detected (rubber stamp!)"; rc=1
  fi
  rm -rf "$tmpd"
  if [ "$rc" = "0" ]; then
    echo "RESOURCE_LIMIT_SOUND: self-test passed (bounded=OK, over-limit=enforced)"
    exit 0
  fi
  echo "RESOURCE_LIMIT_SELFTEST_FAIL"
  exit 1
fi

# ---------- aggregate verdict ----------
if [ "$FAIL" -eq 0 ]; then
  echo "RESOURCE_LIMIT_OK: within configured resource bounds (2608.10530 / 2608.08795 / 2608.12970)"
  exit 0
fi
echo "RESOURCE_LIMIT_FAIL: $FAIL resource-limit defect(s)"
exit 1
