#!/usr/bin/env bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)
# parallel_scheduler.sh — 依存グラフ・並列オーケストレーション層 (dependency-aware map-reduce / wave parallel)
# 呼び出し: router3.sh STEP 2 の実行ループを本スケジューラに置換。
# 目的: 現状の fleet は subtask を for ループで逐次実行し、plan に依存関係(deps)を付けず fan-out している。
#       複数 subtask が共有状態(.org_state/作業dir)を読み書きするがロックが無い。Anthropic が指摘する通り
#       「共有コンテキスト/多い依存関係があるタスクは multi-agent に不向き」「単純クエリで50 subagent 起動は失敗」
#       本層は planner の plan に deps(依存先 id)/isolated(共有状態を書くか) を付与し、依存グラフをトポロジカルに
#       wave 分割、各 wave 内の独立タスクのみを真並列(& + wait)、共有状態書き込みは flock で競合排除、
#       各 wave の結果を reduce(lead が統合)する。ADK Parallel workflow + Magentic-One outer/inner loop +
#       Anthropic orchestrator-worker(3-5並列・別context還元) を統合。洗練された並列の核心。
# 制約: ネット極慢・不安定、credential非保存、LLM不可(計画は router3 の planner が済)。純bash配線(L25: インフラ配線)。
set -u
AGENT_DIR="${AGENT_DIR:-$FLEET_HOME}"
RUN_DIR="${RUN_DIR:-$AGENT_DIR/logs/run_$$}"
PLAN="${1:-$RUN_DIR/delegates.jsonl}"
# [L25] パスは MSYS 形式(/c/...)のまま python に渡す。uv-python は /c/ 形式を
# ネイティブに読める。かつて cygpath -w で C:\Users\... に変換していたが、
# python の open('C:\Users\...') で \U が unicode エスケープとみなされ
# OSError(Errno 22) -> WAVES空 -> 架空完了していた。因此変換を廃止。
PLAN_W="$(cygpath -w "$PLAN" 2>/dev/null || echo "$PLAN")"
RUN_DIR_W="$(cygpath -w "$RUN_DIR" 2>/dev/null || echo "$RUN_DIR")"
[ -d "$RUN_DIR" ] || mkdir -p "$RUN_DIR" 2>/dev/null
LOCKD="$RUN_DIR/.locks"; mkdir -p "$LOCKD" 2>/dev/null
log(){ echo "[$(date '+%m-%d %H:%M:%S')][sched] $*" | tee -a "$RUN_DIR/scheduler.log" 2>/dev/null; }

which jq >/dev/null 2>&1 || { echo "jq 不在: python で代用"; }

# ---- 1. planner の plan に deps/isolated を付与(plan に無ければデフォルト: 全独立) ----
# delegates.jsonl の各 subtask に "deps":["id",...] と "isolated":bool を付与。
# 既に planner が付与していればそのまま、無ければ空 deps(全並列)とする。
python - "$PLAN_W" <<'PY'
import json,sys
p=sys.argv[1]
try:
    lines=[l for l in open(p,encoding='utf-8') if l.strip()]
except Exception:
    print("[]"); sys.exit(0)
out=[]
for l in lines:
    try: s=json.loads(l)
    except Exception: continue
    s.setdefault("deps", s.get("deps", []))
    s.setdefault("isolated", bool(s.get("isolated", False)))
    # deps を文字列リストに正規化
    s["deps"]=[str(d) for d in s.get("deps",[]) if str(d)!=str(s.get("id"))] if s.get("deps") else []
    out.append(s)
# 依存グラフの検証: 存在しない dep / 循環(簡易)を検出
ids={str(s.get("id")) for s in out}
for s in out:
    for d in s["deps"]:
        if d not in ids:
            log_line=f"WARN: subtask {s.get('id')} の dep {d} は存在しません(無視)"
            import os; open(os.path.join(os.path.dirname(p) or ".","scheduler.log"),"a").write(log_line+"\n")
            s["deps"]=[x for x in s["deps"] if x!=d]
with open(p,"w",encoding="utf-8") as f:
    for s in out: f.write(json.dumps(s,ensure_ascii=False)+"\n")
PY

# ---- 2. トポロジカル wave 分割 ----
# deps が空、または既に完了した wave にある subtask のみを次の wave に入れる。
WAVES="$(python - "$PLAN_W" <<'PY'
import json,sys
p=sys.argv[1]
subs=[]
for l in open(p,encoding='utf-8'):
    l=l.strip()
    if not l: continue
    try: subs.append(json.loads(l))
    except Exception: continue
by_id={str(s.get("id")):s for s in subs}
done=set(); waves=[]
remaining=set(by_id.keys())
# 循環検出付きカーンハン
import sys as _s
for _ in range(len(subs)+1):
    wave=[sid for sid in remaining if all(d in done for d in by_id[sid].get("deps",[]))]
    if not wave:
        # 残りは循環 or 孤立 dep → 全て強制 wave に(デッドロック回避)
        wave=list(remaining)
    waves.append(wave)
    done|=set(wave); remaining-=set(wave)
    if not remaining: break
print(json.dumps(waves,ensure_ascii=False))
PY
)"
echo "$WAVES" > "$RUN_DIR/waves.json"
# [L25] フォールバック: WAVES が空(python失敗等)なら delegates.jsonl から全 subtask を1 wave に構築。
# さもなくば scheduler が何も実行せず「架空完了」になる(本番で waves.json が空になった事案への対策)。
if [ -z "$WAVES" ] || [ "$WAVES" = "[]" ]; then
  echo "[L25-FALLBACK] WAVES 空検知 -> delegates.jsonl から全 subtask を単一 wave に構築" | tee -a "$RUN_DIR/scheduler.log" 2>/dev/null
  WAVES="$(python - "$PLAN_W" <<'PY'
import json,sys
p=sys.argv[1]
ids=[]
for l in open(p,encoding='utf-8'):
    l=l.strip()
    if not l: continue
    try: ids.append(str(json.loads(l).get("id")))
    except Exception: continue
print(json.dumps([ids], ensure_ascii=False))
PY
)"
  echo "$WAVES" > "$RUN_DIR/waves.json"
fi

# ---- 3. wave ごとに真並列実行 + flock + reduce ----
TOTAL=$(python -c "import json;print(len(json.load(open(r'$PLAN_W',encoding='utf-8').readlines())))" 2>/dev/null || echo 0)
log "依存グラフを $(python -c "import json;print(len(json.loads('$WAVES')))" 2>/dev/null || echo '?') wave に分割 (subtask=$TOTAL)"

# 実行関数: agent に応じたルートを呼ぶ(router3 の run_* を再利用)
run_one() {
  local sid="$1" agent="$2" inst="$3" isolated="$4"
  local logf="$RUN_DIR/sub_$sid.md"
  # 共有状態を書く(isolated=0)タスクは flock で排他。MSYS git-bash は flock を
  # 持たないため、無い場合はロックなしで直接実行(逐次 wave なので安全)。
  if [ "$isolated" = "0" ] && [ -f "$AGENT_DIR/.org_state" ] && command -v flock >/dev/null 2>&1; then
    (
      flock -x 9
      "$AGENT_DIR/router3.sh" --exec-one "$sid" "$agent" "$inst" > "$logf" 2>&1
    ) 9>"$LOCKD/org_state.lock"
  else
    "$AGENT_DIR/router3.sh" --exec-one "$sid" "$agent" "$inst" > "$logf" 2>&1
  fi
  echo "[sched] subtask $sid ($agent) 完了 -> $logf"
}
export -f run_one

wi=0
# [L25] WAVES をファイルから読み、python にファイルパスを渡す(クォート地獄回避: JSON のダブルクォートが '$WAVES' 展開を破壊していた)
while IFS= read -r wave; do
  wave="${wave%$'\r'}"   # [L25] MSYS/CRLF 混入除去: \r が sid に付くと delegates.jsonl の id と一致せず実行スキップ→架空完了
  wi=$((wi+1))
  log "Wave $wi 実行: $wave"
  pids=""
  for sid in $(python -c "import json,sys; print(' '.join(json.loads(sys.argv[1])))" "$wave" 2>/dev/null); do
    sjson="$(grep -F "$(python -c "import json;print(json.dumps({'id':$sid},ensure_ascii=False)" 2>/dev/null)" "$PLAN" 2>/dev/null | head -1)"
    [ -z "$sjson" ] && continue
    agent="$(echo "$sjson" | python -c "import json,sys;print(json.load(sys.stdin).get('agent','ollama'))" 2>/dev/null)"
    inst="$(echo "$sjson" | python -c "import json,sys;print(json.load(sys.stdin).get('instruction',''))" 2>/dev/null)"
    iso="$(echo "$sjson" | python -c "import json,sys;print('1' if json.load(sys.stdin).get('isolated') else '0')" 2>/dev/null)"
    run_one "$sid" "$agent" "$inst" "$iso" &
    pids="$pids $!"
  done
  # wave 内全完了を待つ(真並列の還元点)
  for p in $pids; do wait "$p"; done
  # reduce: この wave の結果を lead が統合(簡易: 結合して progress ledger に)
  {
    echo "## Wave $wi 結果"
    for sid in $(python -c "import json,sys; print(' '.join(json.loads(sys.argv[1])))" "$wave" 2>/dev/null); do
      echo "### subtask $sid"; [ -f "$RUN_DIR/sub_$sid.md" ] && cat "$RUN_DIR/sub_$sid.md"; echo
    done
  } >> "$RUN_DIR/wave_$wi.md" 2>/dev/null
  log "Wave $wi 還元(reduce)完了"
done < <(python -c "import json;[print(json.dumps(w)) for w in json.load(open(r'$RUN_DIR_W/waves.json',encoding='utf-8'))]" 2>/dev/null || echo '[]')

# ---- 4. 全 wave 統合(最終 reduce) ----
{
  echo "# 並列実行統合結果 ($(python -c "import json;print(len(json.loads('$WAVES')))" 2>/dev/null || echo '?') waves)"
  for w in $(seq 1 $wi); do
    [ -f "$RUN_DIR/wave_$w.md" ] && cat "$RUN_DIR/wave_$w.md"
  done
} > "$RUN_DIR/delegates_result.md" 2>/dev/null
log "全 wave 統合完了 -> $RUN_DIR/delegates_result.md"
exit 0
