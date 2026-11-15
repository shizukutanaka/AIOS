#!/usr/bin/env bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)
# detect_failures.sh — 失敗自動検知ループ
# logs/run_*/sub_*.md を grep でスキャンし、既知の失敗シグネチャを検知する。
# learnings.md に未記録の失敗クラスは新しい L 番号で自動追記し、
# 対応するプランナー注意書きを router3.sh に注入する差分を learnings_diff.txt に生成する。
# Windows パスは cygpath -w で正規化する (L1)。
set -u

AGENT_DIR="$FLEET_HOME"
AGENT_DIR_W=$(cygpath -w "$AGENT_DIR" 2>/dev/null || echo "$AGENT_DIR")
LEARN="$AGENT_DIR/learnings.md"
LEARN_W=$(cygpath -w "$LEARN" 2>/dev/null || echo "$LEARN")
DIFF_W="$AGENT_DIR_W\\learnings_diff.txt"
LOGDIR="$AGENT_DIR/logs"

# 失敗シグネチャ定義（区切りは '~' : 正規表現内の '|' と衝突しないため）
# KEY~正規表現~失敗クラス名~再発防止ルール(プランナー注意書き)
SIGS=$(cat <<'EOF'
CREDIT~requires available credits~LLM APIクレジット不足でサブタスクが無音失敗~課金モデル依存のサブタスクを作るな。ローカル(ollama)かサブスク済(claude)ルートに振れ。~STOP
NOTFOUND~command not found~存在しないコマンドの呼び出し~サブタスクで使うコマンドは事前に command -v で存在確認する手順を含めよ。~STOP
PSOUT~Out-String~powershell コマンドレットの bash 漏れ (L11再発)~powershell.exe -Command の引数は単引用符で全体を囲め。~STOP
TIMEOUT~timed out|タイムアウト~サブタスクのタイムアウト~長時間タスクは分割し、1サブタスクを5分以内に収めよ。~STOP
RATELIMIT~rate limit|429 |usage limit~API/週次上限による停止~上限に達したルートを避け、フォールバックルートを明示せよ。~WAIT
ASKBACK~何をしたいか確認|ご確認ください|どうしますか~実装せず質問を返すサボり (L7再発)~実ファイル変更タスクは hermes(実行型)に振れ。質問返しは不可。~STOP
NOFILE~No such file or directory~パス誤りによるファイル未検出 (L1)~パスは cygpath -w で正規化し、絶対パスで渡せ。~STOP
SSLERR~SSL certificate problem~TLS証明書検証エラーで外部取得が失敗~HTTPS取得は curl -fsSL で失敗検知し、証明書エラー時は代替ミラー/ローカル資産を使え。~STOP
JSONERR~JSONDecodeError|Expecting value~プランナー/ツールのJSON破損~JSON生成は python json.dumps を通せ。手書き連結は禁止。~STOP
PERMDENY~Permission denied~権限不足によるファイル操作失敗~保護パスへ書くな。$HOME 配下か Temp を使え。~STOP
DESKAPP~デスクトップアプリが見つかりません|Argument list too long|TLS handshake timeout|no such host~デスクトップアプリ未検出/外部接続失敗~desktop ルートは起動前に cua-driver list_windows で存在確認し、無ければ hermes へフォールバックせよ。長大引数はファイル経由で渡せ(L1)。~STOP
ZOMBIE~zombie|generate.*hang|tags.*up.*generate.*hang~Ollama zombie(tags応答するがgenerateハングで組織が停止)~generateプローブ(/api/generateで"response"確認)を必ず含め。tags OK だけでは健全とみなすな(L26でensure_ollamaに実装済み)。~STOP
FORKEXH~Resource temporarily unavailable|cygheap read copy failed|fatal error in forked process|fork: retry~git-bash fork枯渇で組織が停止(サブシェル全滅)~fork枯渇時は新しい git-bash セッション/別プロセスツリーで外ループを起動せよ。同一シェル内での再起動は不可。~STOP
CRONSTALL~\[CRONSTALL\]~cron/Hermesスケジューラ停滞で組織が無音停止~外ループ自身が前回実行時刻を記録し、長時間空けば停滞を検知(実装済み)。停滞時は Hermes/cron の稼働を確認し手動起動せよ。~STOP
DETECTORFAULT~\[DETECTORFAULT\]~失敗検知ループ(detect_failures.sh)自身が動作不全~検知器自身の構文/最小動作を外ループが自己検査(実装済み)。検知器が壊れたら即座に detect_failures.sh を修正せよ。検知器が黙ると組織は検知不能になる。~STOP
OUTERCRASH~\[OUTERCRASH\]~外ループ(頂点監視器)が前回クラッシュし自律検知チェーンが機能不全~外ループは正常終了時に heartbeat を書く。呼出はあるのに heartbeat が古い=クラッシュ(実装済み)。fleet_outer.sh の構文/実行時エラーを確認せよ。~STOP
HEALFAIL~\[HEALFAIL\]~自己修復アクションが実行されたが健全性プローブで失敗(直らなかった)~修復を盲信せず、kill_all/起動後にプローブで検証(実装済み)。HEALFAIL は自動修復不能=人間介入が必要。該当インフラ(Ollama/git-bash)を手動修復せよ。~STOP
THROUGHPUTSTALL~\[THROUGHPUTSTALL\]~全プロセス生きているが組織として完了数が増えない(無音の生産性死)~内ループ稼働中に done 増分が連続で0なら停滞検知(実装済み)。タスクが詰まっていないか/内ループが実際に目標を回しているか確認せよ。~STOP
VALUEDECLINE~\[VALUEDECLINE\]~組織が生み出すタスクの価値が連続で低下/閾値未満(無音の価値死: 忙しさ≠価値が崩壊)~val_avg が連続で低下または VVAL_MIN 未満なら検知。対処: 候補選択をより高価値(弱点/エラー/未実装)に寄せ、知識源(learnings/models_catalog)を見直せ。~STOP
EOF
)

echo "[detect] scan dir: $(cygpath -w "$LOGDIR" 2>/dev/null || echo "$LOGDIR")"
FILES=$(ls "$LOGDIR"/run_*/sub_*.md 2>/dev/null)
# 連続同一エラー検知(サーキットブレーカー)用: 前回 circuit 実行以降に新規作成/更新されたログのみを対象とする。
# これにより「過去の単発エラー」が連続検知にカウントされる誤検知を防ぐ（イーロン思考法: 連続=時系列の継続）。
CB_LAST="$AGENT_DIR/.circuit_lastrun"
if [ ! -f "$CB_LAST" ]; then
  # 初回: 全ログを「新規」扱いとして連続検知を開始する（初回スキップはしない）。
  FILES_RECENT="$FILES"
else
  FILES_RECENT=$(find "$LOGDIR" -name 'sub_*.md' -newer "$CB_LAST" 2>/dev/null)
fi
touch "$CB_LAST"   # 次回以降の「前回以降」基準を更新
if [ -z "$FILES" ]; then
  echo "[detect] 対象ログなし。終了。"
  exit 0
fi
echo "[detect] 対象ファイル数: $(echo "$FILES" | wc -l)"

# 既存の最大 L 番号
MAXL=$(grep -oE '^### L[0-9]+' "$LEARN_W" 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1)
[ -z "$MAXL" ] && MAXL=0
NEXTL=$((MAXL + 1))

NEW_RULES=""
DETECTED=0
# 連続同一エラー検知用: DET=今回検知されたKEY, ALLKEYS=全KEY一覧
declare -A DET ALLKEYS

while IFS='~' read -r KEY RE NAME RULE; do
  [ -z "${KEY:-}" ] && continue
  ALLKEYS[$KEY]=1
  HITS=""
  [ -n "$FILES_RECENT" ] && HITS=$(grep -rlEi -- "$RE" $FILES_RECENT 2>/dev/null | head -5)
  CNT=$(echo "$HITS" | grep -c .)
  [ "$CNT" -eq 0 ] && continue
  DETECTED=$((DETECTED + 1))
  DET[$KEY]=1
  echo "[detect] HIT $KEY ($CNT files): $NAME"
  if grep -q "sig:$KEY" "$LEARN_W" 2>/dev/null; then
    echo "[detect]   -> 記録済み (skip)"
    continue
  fi
  SAMPLE=$(echo "$HITS" | head -1)
  SAMPLE_W=$(cygpath -w "$SAMPLE" 2>/dev/null || echo "$SAMPLE")
  {
    echo ""
    echo "### L${NEXTL}. ${NAME} <!-- sig:${KEY} -->"
    echo "- 検知: 自動検知ループ($(date '+%Y-%m-%d %H:%M')) が ${CNT} 件のログで正規表現 \`${RE}\` に一致。"
    echo "- 代表ログ: ${SAMPLE_W}"
    echo "- 再発防止: ${RULE}"
  } >> "$LEARN_W"
  echo "[detect]   -> learnings.md に L${NEXTL} として追記"
  NEW_RULES="${NEW_RULES}L${NEXTL}: ${NAME} -> ${RULE}"$'\n'
  NEXTL=$((NEXTL + 1))
done <<< "$SIGS"

# ---- アイドル除外ガード (L25: インフラ配線 / THROUGHPUTSTALL 偽陽性防止) ----
# 背景: 外ループが「真のアイドル」(goals.md 未完了目標=0 / アクティブ失敗=0) の時は
# 重いフェーズを縮退し、done 増分が連続で0になる(正常な深待機)。detect_failures.sh は
# この状態を「無音の生産性死」と誤検知していた。よって THROUGHPUTSTALL を検知しても、
# 未完了目標数が0ならアイドルと判定しスキップする。
if [ -n "${DET[THROUGHPUTSTALL]:-}" ]; then
  PENDING=$(grep -cE '^- \[ \]' "$AGENT_DIR/goals.md" 2>/dev/null | tr -d '\r' || echo 0)
  if [ "${PENDING:-0}" -eq 0 ]; then
    echo "[detect] THROUGHPUTSTALL をアイドル(深待機)と判定しスキップ: 未完了目標=${PENDING}"
    unset 'DET[THROUGHPUTSTALL]'
    DETECTED=$((DETECTED - 1))
    [ "${DETECTED:-0}" -lt 0 ] && DETECTED=0
  fi
fi

# ---- 連続同一エラー検知 → 自動ストップ (サーキットブレーカー) ----
# 背景: 「連続で同じエラーが出た時に自動でストップが掛かる仕組み」の要求に対応。
# 設計: 各失敗クラス KEY の「連続検知回数」を circuit_breaker.csv に蓄積。
#   今回検知された KEY は連続カウント+1、検知されなかった KEY はカウントリセット(0)。
#   閾値(CB_THRESHOLD, 既定3) 到達で STOP シグナルを発行 → fleet_outer.sh が該当修復を停止。
#   検知が止まれば自動で解除される（カウントが0に戻るため）。
CB_FILE="$AGENT_DIR/circuit_breaker.csv"
CB_FILE_W=$(cygpath -w "$CB_FILE" 2>/dev/null || echo "$CB_FILE")
CB_THRESHOLD="${CB_THRESHOLD:-3}"
STOP_SIGNAL="$AGENT_DIR/stop_signal.txt"
STOP_SIGNAL_W=$(cygpath -w "$STOP_SIGNAL" 2>/dev/null || echo "$STOP_SIGNAL")
: > "$STOP_SIGNAL"   # 毎サイクル作り直す (前回の STOP シグナルをクリア)
# 既存カウントを読み込み (KEY,count)
declare -A CB
if [ -f "$CB_FILE" ]; then
  while IFS=',' read -r k c; do
    [ -z "${k:-}" ] && continue
    # 防御(DETECTORFAULT再発防止): 値が非数値(壊れたCSV等)なら 0 に丸める。
    # さもなくば set -u 下の算術で "PERMDENY" 等の未定義変数として評価され、
    # 検知器自身がクラッシュする(実際に OUTERCRASH の値が "0 PERMDENY,0 ..." 化していた)。
    if [[ ! "$c" =~ ^[0-9]+$ ]]; then c=0; fi
    CB[$k]="${c:-0}"
  done < "$CB_FILE"
fi
CB_TRIPPED=""   # 今回トリップしたクラス一覧
for k in "${!ALLKEYS[@]}"; do
  if [ -n "${DET[$k]:-}" ]; then
    # 今回検知 → 連続カウント+1
    n=$(( ${CB[$k]:-0} + 1 ))
    CB[$k]=$n
    if [ "$n" -ge "$CB_THRESHOLD" ]; then
      CB_TRIPPED="${CB_TRIPPED}${CB_TRIPPED:+,}$k"
      echo "[circuit-breaker] TRIP $k: 連続 ${n} 回検知 (閾値=$CB_THRESHOLD) → STOP シグナル発行" | tee -a "$CB_FILE_W" >/dev/null
    fi
  else
    # 今回未検知 → 連続カウントを緩やかに減衰 (VIGIL/EmoBank 減衰メカニズム: 即0リセット
    # ではなく半減)。これにより「単発エラーの連続誤認」を防ぎつつ、本当の連続故障は
    # 閾値に到達する。純粋 bash インフラ配線 (L25)。
    CB[$k]=$(( ${CB[$k]:-0} / 2 ))
  fi
done
# カウントを保存
: > "$CB_FILE"
for k in "${!CB[@]}"; do
  v="${CB[$k]:-0}"; [[ ! "$v" =~ ^[0-9]+$ ]] && v=0
  echo "$k,$v" >> "$CB_FILE"
done
if [ -n "$CB_TRIPPED" ]; then
  {
    echo "# 自動ストップ シグナル ($(date '+%Y-%m-%d %H:%M'))"
    echo "# 以下の失敗クラスが連続 ${CB_THRESHOLD} 回検知されたため、fleet_outer.sh は該当する自動修復(再起動/dispatch)を停止する。"
    echo "# 検知が止まれば次サイクルで自動解除される。"
    echo "TRIPPED=$CB_TRIPPED"
  } > "$STOP_SIGNAL"
  echo "[circuit-breaker] STOP シグナル発行: $CB_TRIPPED"
else
  echo "[circuit-breaker] 全クラス正常 (連続エラーなし)"
fi

if [ -n "$NEW_RULES" ]; then
  {
    echo "=== 自動検知による learnings -> router3.sh 注入差分 ($(date)) ==="
    echo "対象: router3.sh の PLANNER_PROMPT 内 learnings 注入ブロック (router3.sh:83-84)"
    echo "--- 追記されるプランナー注意書き ---"
    echo "【自動検知された失敗クラス。サブタスク設計時に必ず回避せよ】"
    printf '%s' "$NEW_RULES"
    echo "-----------------------------------"
    echo "注: router3.sh は 'grep -E \"^### L[0-9]+\" learnings.md' で見出しを取り込むため、"
    echo "    learnings.md への上記追記により次回起動時のプランナープロンプトへ自動反映される。"
  } > "$DIFF_W"
  echo "[detect] 新規ルール $(printf '%s' "$NEW_RULES" | grep -c .) 件 -> 差分を $DIFF_W に出力"
else
  {
    echo "=== 自動検知による learnings -> router3.sh 注入差分 ($(date)) ==="
    echo "新規の未記録失敗なし（検知 $DETECTED クラスはすべて learnings.md に記録済み）。"
    echo "現行の注入対象ルール見出し:"
    grep -E '^### L[0-9]+' "$LEARN_W" 2>/dev/null | sed 's/ <!--.*-->//'
  } > "$DIFF_W"
  echo "[detect] 新規の未記録失敗なし（検知 $DETECTED クラスはすべて記録済み）→ 現況を $DIFF_W に出力"
fi

echo "[detect] 完了: 検知クラス=$DETECTED"
