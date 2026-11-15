#!/usr/bin/env bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)
# fleet_loop.sh — Inner Loop (自律実行デーモン)
# 目標キュー goals.md を監視し、未完了目標を自動実行する。
# マスク的思考法: 人間をクロック信号から除外する。
set -u
# パスは Windows 形式に正規化（Python/子プロセス渡し用）
AGENT_DIR_W=$(cygpath -w "$FLEET_HOME" 2>/dev/null || echo "$FLEET_HOME")
AGENT_DIR="$FLEET_HOME"
GOALS="$AGENT_DIR/goals.md"
GOALS_W="$AGENT_DIR_W/goals.md"
DONE="$AGENT_DIR/done.md"
DONE_W="$AGENT_DIR_W/done.md"
ROUTER="$AGENT_DIR/router3.sh"
WORKDIR="$AGENT_DIR/testproj"
MAX_ERRORS=3
SLEEP_EMPTY=15   # キュー空時の待機(秒)
LOG="$AGENT_DIR/fleet_loop.log"
LOG_W="$AGENT_DIR_W/fleet_loop.log"

log(){ echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$LOG_W"; }

# 次の未完了目標を取り出す (最初の - [ ] 行)
next_goal(){
  grep -m1 -E '^\s*- \[ \] ' "$GOALS" 2>/dev/null | sed -E 's/^\s*- \[ \] //'
}

# 目標を完了にする (最初にマッチする未完了行を - [x] に)
mark_done(){
  local g="$1"
  python - "$GOALS_W" <<'PY'
import sys,re
p=sys.argv[1]
try:
    s=open(p,encoding='utf-8').read()
    lines=s.split('\n')
    for i,l in enumerate(lines):
        if re.match(r'^\s*- \[ \] ',l):
            lines[i]=l.replace('- [ ] ','- [x] ',1)
            break
    open(p,'w',encoding='utf-8').write('\n'.join(lines))
except Exception as e:
    print('mark_done err',e)
PY
}


# ---- 単一インスタンス・ロック (L25: インフラ配線 / 二重起動防止) ----
# 外ループの自己修復(fleet_outer.sh:883/892) または手動起動と競合し、同一目標を
# 2 プロセスが同時に router3 に投げる(FORKEXH 予兆)バグを防止。pidfile で排他。
INNER_LOCK="$AGENT_DIR/.inner_lock"
if [ -f "$INNER_LOCK" ]; then
  OLD_INNER=$(cat "$INNER_LOCK" 2>/dev/null | head -1)
  if [ -n "${OLD_INNER:-}" ] && [ "$OLD_INNER" != "$$" ] && kill -0 "$OLD_INNER" 2>/dev/null; then
    echo "[inner-lock] 別インスタンス(PID=$OLD_INNER)が起動中 -> 終了"
    exit 0
  else
    rm -f "$INNER_LOCK" 2>/dev/null
  fi
fi
echo $$ > "$INNER_LOCK"
trap 'rm -f "$INNER_LOCK" 2>/dev/null' EXIT

log "=== Inner Loop START (pid $$) ==="
errors=0
while true; do
  # 連続同一エラー → 自動ストップ (サーキットブレーカー連携)
  # detect_failures.sh が外ループ経由で stop_signal.txt に TRIPPED=KEY1,KEY2 を書く。
  # 内ループはそれを読み、トリップクラスが続行を妨げるもの(ZOMBIE/FORKEXH/RATELIMIT 等)なら停止する。
  STOP_SIGNAL="$AGENT_DIR/stop_signal.txt"
  if [ -f "$STOP_SIGNAL" ]; then
    TRIPPED=$(grep -E '^TRIPPED=' "$STOP_SIGNAL" 2>/dev/null | head -1 | sed 's/^TRIPPED=//')
    if [ -n "$TRIPPED" ]; then
      log "[circuit-breaker] STOP シグナル検知: $TRIPPED → 内ループを停止(連続同一エラーの自動ストップ)"
      log "解除: 該当エラーが連続検知されなくなれば、次サイクルで stop_signal.txt が空になり再開します。"
      break
    fi
  fi

  # 停止フラグ
  if [ -f "$AGENT_DIR/fleet_stop" ]; then log "STOP flag detected. Exiting."; rm -f "$AGENT_DIR/fleet_stop"; break; fi

  GOAL=$(next_goal)
  if [ -z "$GOAL" ]; then
    # キュー空 → 短く待機して再チェック
    sleep "$SLEEP_EMPTY"
    continue
  fi
  log ">>> GOAL: $GOAL"


# ---- 検証可能な完了判定 (L25: インフラ配線 / 徹底的自動化の核心) ----
# 曖昧な「承認」を信じず、目標に付与された done_when オラクルを実行して
# 本当に完了したかを判定する。これが「信任できる自動化」の要件。
# オラクル無しの目標はフォールバック(working-tree 未コミット変更)で判定。
ORACLE=$(bash "$AGENT_DIR/goal_complete.sh" --extract "$GOAL" 2>/dev/null)
ORACLE_RC=$?
ESCAL="$AGENT_DIR/escalations.md"

# 連続同一目標ガード (無限再実行防止): 同一目標を MAX_SAME 回連続で処理しても
# オラクルを満たさなければ、沈黙 SKIP せず「人間判断待ち」としてエスカレーションする。
MAX_SAME=3
SAME_COUNT_FILE="$AGENT_DIR/.inner_same_count"
LAST_GOAL_FILE="$AGENT_DIR/.inner_last_goal"
LAST_G="$(cat "$LAST_GOAL_FILE" 2>/dev/null)"
if [ "$GOAL" = "$LAST_G" ]; then
  SAME_N=$(cat "$SAME_COUNT_FILE" 2>/dev/null || echo 0)
  SAME_N=$((SAME_N+1))
else
  SAME_N=1
  echo "$GOAL" > "$LAST_GOAL_FILE"
fi
echo "$SAME_N" > "$SAME_COUNT_FILE"

# ボスモードで実行（部下が実作業をする）
OUT=$(bash "$ROUTER" "$GOAL" "$WORKDIR" 2>&1)
RC=$?
echo "$OUT" >> "$LOG"

# オラクルで完了判定 (信頼できない router3 の自己申告は無視)
if [ -n "$ORACLE" ]; then
  if bash "$AGENT_DIR/goal_complete.sh" --check "$GOAL" >/dev/null 2>&1; then
    mark_done "$GOAL"
    log "<<< DONE: $GOAL (oracle PASS: $ORACLE)"
    echo "- [$(date '+%m-%d %H:%M')] $GOAL" >> "$DONE_W"
    git -C "$AGENT_DIR" add -A 2>/dev/null
    git -C "$AGENT_DIR" commit --no-verify -m "feat(agent): $GOAL" >/dev/null 2>&1 || true
    errors=0
    continue
  fi
  # オラクル未達: エラー扱い
  errors=$((errors+1))
  log "!!! ORACLE-FAIL on: $GOAL (errors=$errors) oracle=[$ORACLE]"
else
  # オラクル無し: フォールバック(working-tree 未コミット変更があれば完了とみなす)
  if [ -n "$(git -C "$AGENT_DIR" status --porcelain 2>/dev/null | head -1)" ]; then
    mark_done "$GOAL"
    log "<<< DONE: $GOAL (fallback: 未コミット変更を検出 / オラクル無し)"
    echo "- [$(date '+%m-%d %H:%M')] $GOAL" >> "$DONE_W"
    git -C "$AGENT_DIR" add -A 2>/dev/null
    git -C "$AGENT_DIR" commit --no-verify -m "feat(agent): $GOAL" >/dev/null 2>&1 || true
    errors=0
    continue
  fi
  errors=$((errors+1))
  log "!!! NO-ORACLE-NO-ARTIFACT on: $GOAL (errors=$errors)"
fi

# 連続失敗が上限に達 → エスカレーション(沈黙 SKIP 廃止)
if [ "$errors" -ge "$MAX_ERRORS" ] || [ "$SAME_N" -ge "$MAX_SAME" ]; then
  {
    echo ""
    echo "### ESCALATION $(date '+%Y-%m-%d %H:%M')"
    echo "- 目標: $GOAL"
    echo "- 連続失敗: $errors / 上限 $MAX_ERRORS"
    echo "- 同一目標連続: $SAME_N / 上限 $MAX_SAME"
    echo "- オラクル: ${ORACLE:-'(なし)'}"
    echo "- 最終 router3 出力(末尾):"
    echo '```'
    echo "$OUT" | tail -15
    echo '```'
    echo "- 状態: 人間判断待ち (自動化はこの目標を完了できず。目標定義/オラクル/部下のいずれかを修正せよ)"
  } >> "$ESCAL"
  log "[escalate] $GOAL → $ESCAL (人間判断待ち)"
  mark_done "$GOAL"   # 再処理を防ぐ
  errors=0
  echo "$SAME_N" > "$SAME_COUNT_FILE"
  sleep 5
  continue
fi
sleep 5
done
log "=== Inner Loop END ==="
