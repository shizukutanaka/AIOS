#!/usr/bin/env bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)
# boss_trigger_guard.sh — continuous-same-trigger circuit breaker for boss-mode.
# Records the signature of each boss invocation; hard-holds after N consecutive
# IDENTICAL ones. Non-destructive: only writes its own state file. A new/different
# signature resets the counter to 1 (so legitimate new tasks are never blocked).
#
# Exit 0 = PASS (dispatch allowed). Exit 1 = HOLD (do NOT dispatch; require new directive).
# Integrate by calling this BEFORE router3.sh dispatch:
#   bash boss_trigger_guard.sh "ai-fleet-boss|イーロン・マスクの思考法で続けて" || { echo HOLD; exit; }
set -u
STATE="${BOSS_GUARD_STATE:-$FLEET_HOME/.boss_trigger_guard}"
N="${BOSS_GUARD_N:-3}"
SIG="$*"
[ -z "$SIG" ] && { echo "no signature provided"; exit 1; }

# Continuation markers = explicit user momentum ("continue / keep going"). Per
# memory policy '続けて/おまかせ = momentum, NOT re-consent', these are legitimate
# NEW directives, not a runaway identical-trigger loop. So a genuine circuit breaker
# still caps them — but at a HIGHER budget (BOSS_GUARD_CONT_N) — so autonomous
# continuation is not throttled at the strict N=3, while a true infinite loop is
# still impossible. A NON-continuation identical trigger still trips at N=3 (real
# protection against a stuck orchestrator repeating itself with no user intent).
CONT_MARKERS='続けて|おまかせ|continue|keep going|継続|もう少し|いって|イーロン|keep'
if printf '%s' "$SIG" | grep -qiE "$CONT_MARKERS"; then
  N="${BOSS_GUARD_CONT_N:-15}"
fi

# Hash the signature so the stored value can never collide with our '|' delimiter.
HASH="$(printf '%s' "$SIG" | md5sum | cut -d' ' -f1)"
[ -z "$HASH" ] && HASH="$SIG"   # fallback if md5sum unavailable

LAST=""; COUNT=0
if [ -f "$STATE" ]; then
  LAST="$(cut -d'|' -f1 "$STATE" 2>/dev/null)"
  COUNT="$(cut -d'|' -f2 "$STATE" 2>/dev/null || echo 0)"
  case "$COUNT" in (*[!0-9]*) COUNT=0;; esac
fi

if [ "$HASH" = "$LAST" ]; then
  COUNT=$((COUNT+1))
else
  COUNT=1
fi
printf '%s|%s\n' "$HASH" "$COUNT" > "$STATE"

if [ "$COUNT" -ge "$N" ]; then
  echo "HOLD: identical trigger seen ${COUNT} consecutive times (limit ${N}) — release via a genuinely new directive"
  exit 1
fi
echo "PASS: trigger #${COUNT} (limit ${N})"
exit 0
