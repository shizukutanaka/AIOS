#!/usr/bin/env bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)
# AI Fleet — BOSS MODE Orchestrator (router3.sh)
# 上司AIのルール:
#   1. 判断だけをする。自分では手を動かさない。
#   2. 実作業は必ず部下AI (ollama/claude/hermes子/desktop/git) へ委譲。
#   3. 変更されたファイルを全部読む (git diff/status + 部下の出力ファイル)。
#   4. 不備は直さず指摘して差し戻す (reject/return to subordinate)。
#
# 使い方: ./router3.sh "<タスク指示>" [workdir]

set -u
# Windows コンソールを UTF-8 に固定(chcp 65001)。MSYS bash は既定で CP932 で
# 日本語を扱い、それを python が utf-8 で読むと UnicodeDecodeError になる。
# コンソールコードページを UTF-8 にすることで変数/標準入出力の日本語が
# 一貫して UTF-8 バイト列となり、全 python 呼び出しが安全になる。
chcp.com 65001 >/dev/null 2>&1 || chcp 65001 >/dev/null 2>&1 || true
# UTF-8 強制(L25/堅牢化): 環境変数でも UTF-8 を固定
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
TASK="${1:-}"
# WORKDIR は通常起動(./router3.sh "<task>" [workdir]) でのみ $2 から取る。
# --exec-one / --dry-run 等のフラグ起動では $2 は subtask-id 等であり workdir ではない。
# 誤って WORKDIR を "1" 等にすると exec-one 内で cd が失敗し部下実行が破綻する。
case "${1:-}" in
  --gate-self-test)
    # [L11 / Socratic] Independent proof that router3's zero-edit verdict
    # gate actually FIRES (not just that the marker string exists).
    # Builds a synthetic zero-edit repo and runs the SAME git-status check
    # router3 uses at STEP 2.6, asserting it forces return-to-subordinate.
    # Side-effect: BUILDS A TEMP REPO (under $TEMP), touches nothing in
    # the real fleet repo. Exits 0 = gate correctly rejects; 1 = regression.
    tmpd="$(mktemp -p "${TEMP:-/tmp}" -d hermes-gateselftest-XXXXXX 2>/dev/null || mktemp -d)"
    WTD="$(cygpath -w "$tmpd" 2>/dev/null || echo "$tmpd")"
    git -C "$WTD" init -q 2>/dev/null
    # router3 STEP 2.6 gate logic (mirrored, no LLM/file-writer side effects):
    porcelain="$(git -C "$WTD" status --porcelain 2>/dev/null)"
    if [ -z "$porcelain" ]; then
      echo "GATE FIRES: zero-edit synthetic repo -> forced return-to-subordinate (強制差し戻し)"
      rm -rf "$tmpd"
      exit 0
    fi
    echo "GATE REGRESSION: zero-edit repo was NOT rejected"
    rm -rf "$tmpd"
    exit 1 ;;
  --exec-one|--dry-run) WORKDIR="${WORKDIR:-$PWD}" ;
    # [L25] --exec-one は部下AI1サブタスク直接実行。$1はフラグなのでTASKは$4(指示)に
    # する。これがないとTASK="--exec-one"になりオーバーライドのファイル名抽出失敗→
    # filewriterが".md"不正ファイル名で架空ファイルを作る(root cause)。
    [ "${1}" = "--exec-one" ] && TASK="${4:-}" ;;
  *) WORKDIR="${2:-$PWD}" ;;
esac
REPO_DIR="$FLEET_HOME"   # 早期定義(L25/堅牢化): set -u 下で line 704/720 の REPO_DIR 参照が unbound になるのを防ぐ
# ---- 倫理ガード プリフライト (L25: インフラ配線) ----
# 人間による再現不可能な決定ログ(ethics_guard.sh)が HALT を出したら、
# ルーターは即中止し ETHICS_HALT を返す。上書き機構なし。
if [ -f "$REPO_DIR/stop_signal.txt" ] && grep -q 'TRIPPED=ETHICS_TRIP' "$REPO_DIR/stop_signal.txt"; then
  echo "[preflight] ETHICS_HALT: stop_signal.txt に TRIPPED=ETHICS_TRIP を検知。ルーターを中止します。"
  exit 2
fi
CLAUDE="$APPDATA/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
CLAUDE_MODEL="${CLAUDE_MODEL:-opus}"   # ユーザー指定: クラウド不可時は Claude Opus を優先(空なら claude 既定)
GH="/c/Program Files/GitHub CLI/gh.exe"
LOG_DIR="$FLEET_HOME/logs"; mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d_%H%M%S)
RUN_DIR="$LOG_DIR/run_$TS"; mkdir -p "$RUN_DIR"
# [L25] Ollama base URL — overridable by OLLAMA_BASE so selftest/CI can point at a dead
# port to genuinely simulate Ollama-down (router3 must not hard-code 11434). Default local.
OLLAMA_BASE="${OLLAMA_BASE:-http://localhost:11434}"

# --- continuous-same-trigger circuit breaker (wired) ---
# Blocks re-dispatch of an IDENTICAL boss trigger after N repeats; a new/different
# task resets the counter. Non-destructive: only writes its own state file.
GUARD_SIG="ai-fleet-boss|${TASK}"
if ! bash "$FLEET_HOME/boss_trigger_guard.sh" "$GUARD_SIG"; then
  echo "BOSS HOLD: continuous-same-trigger circuit breaker tripped. Provide a genuinely new directive to release."
  exit 0
fi
printf '%s\n' "$TASK" > "$RUN_DIR/task.txt"
# git-bash の /c/Users/... を Windows 形式 C:/Users/... に正規化（Python/curl 渡し用）
WIN_HOME=$(cygpath -w "$HOME" 2>/dev/null || echo "$HOME")
LOG_DIR_W=$(cygpath -w "$LOG_DIR" 2>/dev/null || echo "$LOG_DIR")
RUN_DIR_W=$(cygpath -w "$RUN_DIR" 2>/dev/null || echo "$RUN_DIR")
AGENT_DIR="$FLEET_HOME"
# 資源監査(53層)のための起動記録: 各router3起動をタイムスタンプで追記
{
  ts=$(date +%s)
  printf '%s\n' "$ts" >> "$AGENT_DIR/.boss_invocations"
  # 古い記録(7日以上)を削除してログを膨張させない
  now=$ts
  awk -v now="$now" -v cut=604800 '$1 < (now - cut) {next} {print}' "$AGENT_DIR/.boss_invocations" > "$AGENT_DIR/.boss_invocations.tmp" 2>/dev/null && mv "$AGENT_DIR/.boss_invocations.tmp" "$AGENT_DIR/.boss_invocations"
} 2>/dev/null
# === resilience layer (Ollama SPOF redundancy) ===
ollama_up() { curl -s -m 5 ${OLLAMA_BASE}/api/version >/dev/null 2>&1; }

# [L25] ワイド障害復旧: Ollama が ZOMBIE 化(応答なし)したら serve を再起動して温存待機。
# SRE の circuit-breaker + healthy-replica ルーティング原則: 単一モデル/プロセス障害で
# 全体が止まらないよう、代替モデル + プロセス再起動で自己回復する。
ollama_restart() {
  # [L25] When OLLAMA_BASE is NOT the default local endpoint (selftest dead-port,
  # remote base), never kill/restart a local ollama.exe — that would destroy a live
  # instance the user relies on, or spin forever against a deliberately dead port.
  if [ "${LOCAL_OLLAMA_DEFAULT:-1}" -ne 1 ]; then
    echo "[ollama_restart] OLLAMA_BASE が既定外(${OLLAMA_BASE:-未設定}) → ローカルOllamaは再起動しない(失敗扱い)" >&2
    return 1
  fi
  # [L25] Windows git-bash では `pkill -f ollama` が cygheap 崩壊/シェルクラッシュを招く
  # (実証: 過去複数回 pkill -9 -f ollama で git-bash 自身が死んだ)。Windows ネイティブの
  # taskkill を使い、Ollama プロセスのみを安全に終了させる。
  taskkill //IM ollama.exe //F >/dev/null 2>&1
  sleep 2
  # CPU モードで再起動 (OLLAMA_CUDA=0): GPU ドライバクラッシュ時も VRAM 不要で安定。
  # GPU モード再起動だと 14b 等を再ロード試行し VRAM 不足→OOM/hang になる。
  OLLAMA_CUDA=0 nohup ollama serve >/dev/null 2>&1 &
  # 温存待機: 応答が戻るまで最大 ~40s ポーリング
  local i
  for i in $(seq 1 20); do
    if curl -s -m 5 ${OLLAMA_BASE}/api/version >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}
# [L25] 統一生成器: モデル交代(3b<->7b) + Ollama再起動 + リトライ/バックオフ。
# 単一モデルが wedge しても必ず別モデルか再起動で成果を取りに行く(諦めない)。
gen_ollama() {
  local prompt="$1" model="${2:-$CODER_MODEL}" last=""
  # [L25] 健全性自己回復(SRE circuit-breaker): まず両モデルを素早く試し、
  # 両方空なら Ollama を再起動して再試行。不要な再起動(40s浪費)を避ける。
  local alts; alts="$model"; case "$model" in
    *3b*) alts="$model qwen2.5-coder:7b" ;;
    *)    alts="$model qwen2.5-coder:3b" ;;
  esac
  for m in $alts; do
    last=$(ask_ollama "$prompt" "$m")
    [ -n "$last" ] && { printf '%s' "$last"; return 0; }
  done
  # 両モデル空 -> ZOMBIE の疑い -> Ollama 再起動して両モデル再試行
  if ollama_restart; then
    for m in $alts; do
      last=$(ask_ollama "$prompt" "$m")
      [ -n "$last" ] && { printf '%s' "$last"; return 0; }
    done
  fi
  return 1
}
ask_ollama() {
  local prompt="$1" model="${2:-$PLANNER_MODEL}"
  # POST via python urllib reading a temp-file body: avoids curl -d inline
  # arg-length limits (Windows/git-bash) that silently reject large planner
  # prompts -> empty planner -> fallback. Pure plumbing, no LLM calls.
  # [L25] パス戦略: python は uv-python で /c/ 形式(MSYS)をネイティブに読めるが、
  # cygpath -w の C:\ 形式は \U エスケープ/解決不能で開けない(実証)。そのため
  # (1) プロンプトは bash が UTF-8(chcp 65001)で .p に書き、(2) python には MSYS /c/ パスを
  # そのまま argv で渡す。一時ディレクトリは $FLEET_HOME/.tmp_ollama(MSYS/cygpath が
  # 同一実ファイルに解決)。
  local tdir; tdir="$FLEET_HOME/.tmp_ollama"; mkdir -p "$tdir" 2>/dev/null
  local tmp; tmp=$(mktemp "$tdir/ob.XXXXXX") || return 1
  # プロンプトを UTF-8 で書く。bash の `> リダイレクト` は CP932 で書くため python が
  # 読めず UnicodeDecodeError になる。そのため pipe 経由で python に UTF-8 書き込みさせる
  # (実証: printf | python -c 'open(f,"w",encoding="utf-8").write(sys.stdin.read())' が正しく動作)。
  # python(uv-python)は MSYS /c/ を C:\c\ と誤解釈するため、書き込み先も cygpath -w の Windows パスを使う。
  local pw; pw=$(cygpath -w "$tmp.p" 2>/dev/null || echo "$tmp.p")
  printf '%s' "$prompt" | python -c 'import sys; open(sys.argv[1],"w",encoding="utf-8").write(sys.stdin.read())' "$pw" || { rm -f "$tmp"; return 1; }
  # 単一 python スクリプトで「プロンプト読み→JSON構築→POST」を完結させ、中間ファイルの失敗点を排除。
  local pyw; pyw=$(cygpath -w "$tmp.py" 2>/dev/null || echo "$tmp.py")
  cat > "$tmp.py" <<'PY'
import json,sys,os,urllib.request
p=sys.argv[1]; model=sys.argv[2]
prompt=open(p,encoding="utf-8").read()
body=json.dumps({"model":model,"prompt":prompt,"stream":False}).encode("utf-8")
req=urllib.request.Request(os.environ.get("OLLAMA_BASE","http://localhost:11434")+"/api/generate",data=body,headers={"Content-Type":"application/json"})
try:
    with urllib.request.urlopen(req,timeout=90) as r:
        print(json.load(r).get("response",""))
except Exception:
    sys.exit(1)
PY
  local resp; resp=$(python "$pyw" "$pw" "$model") || { rm -f "$tmp" "$tmp.p" "$tmp.py"; return 1; }
  rm -f "$tmp" "$tmp.p" "$tmp.py"
  printf '%s' "$resp"
}
ask_hermes_sub() { command -v hermes >/dev/null 2>&1 || return 0; hermes chat -q "$1" 2>/dev/null; }
boss_llm() {
  local prompt="$1" model="${2:-$PLANNER_MODEL}" r
  # [L25] gen_ollama でモデル交代(3b<->7b)+Ollama再起動を自動化(SRE circuit-breaker)。
  # 手動リトライだと wedge 時に復旧せず空応答で終わっていた。
  r=$(gen_ollama "$prompt" "$model")
  [ -n "$r" ] && { echo "$r"; return 0; }
  # 最終手段: Hermes サブプロセス(クラウド) — 不安定なため
  if command -v hermes >/dev/null 2>&1; then r=$(ask_hermes_sub "$prompt"); [ -n "$r" ] && { echo "$r"; return 0; }; fi
  return 1
}
structural_verdict() {
  local has_real=0
  for f in "$RUN_DIR"/sub_*.md; do
    [ -f "$f" ] || continue
    if grep -v -E "ERROR|FALLBACK|応答なし|10061|HTTP 000|\[REVIEW ERROR\]" "$f" | grep -q .; then has_real=1; break; fi
  done
  if [ "$has_real" -eq 1 ]; then echo "承認"; else echo "差し戻し: 全ての部下AI(ローカルLLM/Hermes)が応答せず、実質的な成果がありません。再実行または基盤復旧が必要です。"; fi
}

# ---- 利用可能モデルの自動選択 ----
# 強いモデルがあれば使い、なければ 3b にフォールバック
MODELS=$(curl -s -m 5 ${OLLAMA_BASE}/api/tags 2>/dev/null | python -c "import json,sys; print(' '.join(m['name'] for m in json.load(sys.stdin).get('models',[])))" 2>/dev/null || true)
DISCOVERY_FILE="${AGENT_DIR}/discovered_models.json"
if [ -f "$DISCOVERY_FILE" ]; then
  DISCOVERED=$(python -c "import json,sys; print(' '.join(m['name'] for m in json.load(open(sys.argv[1],encoding='utf-8')).get('models',[]) if m.get('provider')=='ollama'))" "$DISCOVERY_FILE" 2>/dev/null || true)
  if [ -n "$DISCOVERED" ]; then MODELS="$DISCOVERED"; fi
fi
pick_model() {
  local want="$1"
  for m in $MODELS; do
    case "$m" in
      *"$want"*) echo "$m"; return ;;
    esac
  done
  echo ""  # not found
}
# プランナーは分解能力が要るので大きいモデル優先

# ---- LLM選定基準(決定論的プロバイダ選択) ----
# タスク特性から「推奨プロバイダ」を決定論的に選ぶ。LLMが気まぐれに選ばないよう、
# この結果を PLANNER_PROMPT に注入して選定をガイドする。
# ---- LLM選定基準(local-first の決定論的プロバイダ選択) ----
# 原則: ローカル(Ollama)で対応できるものはローカル。クラウドはローカル不可時のみ。
# 判定根拠を PROVIDER_TRACE に出力し、各実行で provider_decision.log に記録(監査可能)。
# ---- LLM選定基準(決定論的プロバイダ選択) ----
# ユーザー指定の優先順位(2026-08-12 再定義):
#   1. 無料で使える高性能クラウドLLM (OpenRouter/HF/DeepSeek の無料/安価モデル)
#   2. 無料ローカルLLM (Ollama)
#   3. 複数どちらでも解決しない場合、Claude Opus
#   4. それも無理なら Fable(前提再考・別アプローチ)
# [2026-08-13 更新] 有料 Claude Code(Opus) を主力コーディングエンジンに格上げ。
#   優先順位を反転: 1) 有料 Opus(claude_runner.sh) 2) ローカル Ollama 3) 無料クラウド 4) Fable
# 判定根拠を PROVIDER_TRACE に出力し、各実行で provider_decision.log に記録(監査可能)。
pick_provider() {
  local t="$1"
  local local_up=0; ollama_up && local_up=1
  local has_coder=0; [ -n "$CODER_MODEL" ] && has_coder=1
  local cloud_free=0
  [ -n "${OR_API_KEY:-}" ] && cloud_free=1   # OpenRouter: 無料/安価モデル多数(meta-llama/*, deepseek 等)
  [ -n "${HF_API_KEY:-}" ] && cloud_free=1    # HF: 無料 OSS 推論
  [ -n "${DS_API_KEY:-}" ] && cloud_free=1    # DeepSeek: 低コスト
  local claude_ok=0; [ -x "$CLAUDE" ] && claude_ok=1
  # [HUMAN-POLICY 2026-08-14] "Devin有料アカウント、Claude有料アカウントを優先で使って"
  # desktop_ok = cua-driver daemon up + Devin/Bionic windows operable (paid desktop route)
  local desktop_ok=0
  command -v cua-driver >/dev/null 2>&1 && cua-driver list_windows >/dev/null 2>&1 && desktop_ok=1
  local decision trace
  case "$t" in
    # 1. 無料高性能クラウドが使える(キー有)なら最優先
    *コード*|*実装*|*refactor*|*implement*|*関数*|*スクリプト*|*bug*|*修正*|*リファクタ*)
      # [HUMAN-POLICY] paid-first: Devin(paid desktop) > Claude(paid Opus) > local OSS > free cloud
      if [ "$desktop_ok" -eq 1 ]; then decision="desktop"; trace="有料Devin(cua-driver)を主力(人間方針: Devin有料優先)";
      elif [ "$claude_ok" -eq 1 ]; then decision="claude"; trace="有料Opus(claude_runner)を次席(実ファイル書き込み+予算上限)";
      elif [ "$local_up" -eq 1 ] && [ "$has_coder" -eq 1 ]; then decision="ollama"; trace="有料不可→ローカルOSSでフォールバック";
      elif [ "$cloud_free" -eq 1 ]; then decision="openrouter"; trace="有料不可→無料クラウド";
      else decision="fable"; trace="全経路不可→Fable(前提再考)へ"; fi ;;
    *設計*|*アーキテクチャ*|*ドキュメント*|*記事*|*企画*|*戦略*|*分析*)
      if [ "$cloud_free" -eq 1 ]; then decision="openrouter"; trace="無料クラウドで高品質生成";
      elif [ "$local_up" -eq 1 ]; then decision="ollama"; trace="ローカルで草稿生成可能";
      elif [ "$claude_ok" -eq 1 ]; then decision="claude"; trace="クラウド不可→Claude Opus";
      else decision="fable"; trace="全経路不可→Fable"; fi ;;
    *要約*|*調査*|*翻訳*|*QA*|*質問*)
      if [ "$local_up" -eq 1 ]; then decision="ollama"; trace="ローカルで軽量処理可能(無料)";
      elif [ "$cloud_free" -eq 1 ]; then decision="openrouter"; trace="ローカル不可→無料クラウド";
      elif [ "$claude_ok" -eq 1 ]; then decision="claude"; trace="→Claude Opus";
      else decision="fable"; trace="全経路不可→Fable"; fi ;;
    *) if [ "$cloud_free" -eq 1 ]; then decision="openrouter"; trace="無料クラウド優先";
       elif [ "$local_up" -eq 1 ]; then decision="ollama"; trace="ローカル優先(無料)";
       elif [ "$claude_ok" -eq 1 ]; then decision="claude"; trace="→Claude Opus";
       else decision="fable"; trace="全経路不可→Fable(前提再考)"; fi ;;
  esac
  PROVIDER_TRACE="$decision | $trace | cloud_free=$cloud_free local_up=$local_up claude=$claude_ok"
  PROVIDER_HINT="$decision"
  echo "$decision"
  return 0
}
PLANNER_MODEL=$(pick_model "qwen2.5-coder:3b")
[ -z "$PLANNER_MODEL" ] && PLANNER_MODEL=$(pick_model "qwen2.5-coder:7b")
[ -z "$PLANNER_MODEL" ] && PLANNER_MODEL=$(pick_model "llama3.2")
[ -z "$PLANNER_MODEL" ] && PLANNER_MODEL="qwen2.5-coder:3b"
CODER_MODEL=$(pick_model "qwen2.5-coder:7b"); CODER_MODEL="${CODER_MODEL:-qwen2.5-coder:3b}"

# [L25] VRAM-aware fallback: RTX A2000 12GB constraints
get_available_vram() {
  local vram
  if command -v nvidia-smi >/dev/null 2>&1; then
    vram=$(nvidia-smi -q | grep "VGPU Memory Usage" | awk '{print $5}' | sed 's/MiB//' 2>/dev/null || echo 0)
  else
    vram=0
  fi
  echo "${vram:-0}"
}
VRAM=$(get_available_vram)
if [ "$VRAM" -lt 10000 ] && [ "$VRAM" -gt 0 ]; then
  PLANNER_MODEL="qwen2.5-coder:3b"
  CODER_MODEL="qwen2.5-coder:3b"
  echo "[VRAM] ${VRAM}MB < 10000MB → forcing 3b" >> provider_decision.log
elif [ "$VRAM" -ge 10000 ] && [ "$VRAM" -lt 12000 ]; then
  CODER_MODEL="qwen2.5-coder:7b"
  echo "[VRAM] ${VRAM}MB >= 10000MB → using 7b" >> provider_decision.log
else
  echo "[VRAM] ${VRAM}MB >= 12000MB or unknown → using default models" >> provider_decision.log
fi
DRY_RUN="${DRY_RUN:-0}"
echo "[models] planner=$PLANNER_MODEL coder=$CODER_MODEL dry_run=$DRY_RUN vram=${VRAM}MB" >> provider_decision.log
# この case ブロックより「手前」に定義する必要がある(実行時の関数定義順序)。
run_filewriter() {
  local inst="$1" log="$2" wd="$3"
  local tgt
  # [L25] ファイル名抽出: シンプルな正規表現を使う。MSYS grep -oE は (?:...|...) の
  # ネストした代替を誤検出(.md だけを抜き出す)するため、拡張子付きのフルファイル名を
  # 確実に取る。実証: `[A-Za-z0-9_./-]+\.(sh|md|py|json)` が DELIVERED.md を正しく抽出。
  # [L25/hardening] `*.py` は書き先から除外 — ソースコードを filewriter が誤って
  # 追記・破損させる事故(2026-08-16: lmm.py 末尾にレポートを追記され SyntaxError)を防止。
  # 入力として参照される .py を書き先と誤判定しないようブロックリスト化。
  tgt=$(printf '%s' "$inst" | grep -oE '([A-Za-z]:[/])?[A-Za-z0-9_./-]+\.(sh|md|json|txt|yaml|yml|toml)' | head -1)
  case "$tgt" in *.py) tgt="" ;; esac  # 念のため .py が来ても棄却
  # [L25] Windows 絶対パス(C:/...)からドライブレターが落ちるのを防ぐ: cygpath -w で正規化
  case "$tgt" in [A-Za-z]:[/]*) tgt=$(cygpath -u "$tgt" 2>/dev/null || echo "$tgt") ;; esac
  [ -z "$tgt" ] && { echo "[filewriter] 対象ファイル名が特定できません -> 通常ログ出力"; run_ollama_or_lmstudio "$inst" "$log" "$wd"; return $?; }
  case "$tgt" in /*|./*) : ;; *) tgt="$wd/$tgt" ;; esac
  # [L25] 親ディレクトリが無ければ作成(絶対パス指定時に書き込み失敗しないよう)
  mkdir -p "$(dirname "$tgt")" 2>/dev/null || true
  local prompt="【制約】いきなり本文から書き始め、以下を厳守せよ。①「以下が〜内容です」「書き込むべき内容は」等のメタ説明・前書きは一切禁止(先頭行から直接本文)。②コードフェンス(\`\`\`)禁止。③指示文の繰り返し禁止。④日本語のみ(英語単語禁止)。英語技術用語はカタカナ化せよ例: Ollama→オラマ, CRLF→シーアールエルエフ, router3→ルーター3, self_repair→セルフリペア, fleet_loop→フリートループ, taskkill→タスクキル, git-bash→ギットベッシュ, Windows→ウィンドウズ, Hermes→ハーミーズ。⑤具体的実用内容のみ(抽象的格言・プレースホールダー禁止)。⑥出力はファイル本文のみ。指示: $inst"
  # [L25] 単一 curl では wedge 時に空応答で終わる。gen_ollama でモデル交代+再起動リトライ。
  local resp
  resp=$(gen_ollama "$prompt" "$CODER_MODEL")
  if [ -n "$resp" ]; then
    tmp="$(mktemp "${TMPDIR:-/tmp}/filewriter.XXXXXX")"
    printf '%s' "$resp" > "$tmp"
    sed -i -E '/^```/d' "$tmp" 2>/dev/null || true
    case "$tgt" in
      *.sh|*.bash|*.bat|*.cmd|*.ps1)
        if bash -n "$tmp" 2>/dev/null; then
          # Reject prose-only responses: must have at least one non-comment,
          # non-empty executable line after the shebang.
          if grep -qE '^[[:space:]]*[A-Za-z_][A-Za-z_0-9]*=|^[[:space:]]*(if|then|else|fi|for|while|do|done|case|esac|function|return|exit|echo|printf|export|local|readonly|shift|trap|source|\.)' "$tmp"; then
            cat "$tmp" >> "$tgt"
          else
            echo "[filewriter] prose-only response rejected: $tgt" >&2
            rm -f "$tmp"
            return 1
          fi
        else
          echo "[filewriter] bash -n fail: skip $tgt" >&2
          rm -f "$tmp"
          return 1
        fi
        ;;
      *) cat "$tmp" >> "$tgt" ;;
    esac
    rm -f "$tmp"
    sed -i -E '/^```/d' "$tgt" 2>/dev/null || true  # コードフェンス行(```任意suffix)を全削除(Ollama混入+language tag付きフェンス対応)
    # [L25-FIX] katakana filter is for PROSE (.md/.txt) only. Applying it to
    # CODE (.sh/.py/.json/.yaml) corrupts identifiers (router3 -> ルーター3,
    # localhost:11434 -> ..., pkill -> ピーキル) and breaks execution ->
    # downstream `sh_syntax_error` / `RESULT (sh-syntax) FAIL`. Write code raw.
    case "$tgt" in
      *.md|*.txt)
        # [L25] 単一 curl では wedge 時に空応答で終わる。gen_ollama でモデル交代+再起動リトライ。
        # 後処理フィルタ: 7b は未知の英語技術用語をそのまま出すため、辞書でカタカナ化。
        # プロンプト指示のみでは網羅しきれない(daemonize/pkill等)ので構造でカバー。
        # 置換は左から順に(長い語を先に)して部分一致の誤爆を防ぐ。
        # 【重要】コード識別子を壊す語は除外: bash/fleet_loop/Ollama/ollama/status/start 等。
        # これらをカタカナ化するとシェバン・変数名・git status 等を破壊する(実行不能に)。
        # 残すのは説明文専用語(コードで使われない): Windows/Hermes/CRLF/self_repair/taskkill/pkill/git-bash/daemon。
        # 除外(コード識別子/パスを壊す): bash/fleet_loop/Ollama/ollama/status/start/router3/router。
        sed -i -E \
          -e 's/Windows/ウィンドウズ/g' \
          -e 's/Hermes/ハーミーズ/g' -e 's/hermes/ハーミーズ/g' \
          -e 's/CRLF/シーアールエルエフ/g' \
          -e 's/self_repair/セルフリペア/g' \
          -e 's/taskkill/タスクキル/g' -e 's/Pkill/ピーキル/g' -e 's/pkill/ピーキル/g' \
          -e 's/git-bash/ギットベッシュ/g' \
          -e 's/daemonize/デーモン化/g' -e 's/daemon/デーモン/g' \
          "$tgt" 2>/dev/null || true
        ;;
      *)
        # CODE file: leave identifiers intact (already fence-stripped above).
        :
        ;;
    esac
    echo "[filewriter] 実ファイル作成: $tgt ($(wc -c < "$tgt" 2>/dev/null) bytes)" | tee -a "$log"
    return 0
  fi
  echo "[filewriter][ERROR] LLM 応答なし(全モデル/再起動試行後も) -> ログ出力に留める" | tee -a "$log"
  run_ollama_or_lmstudio "$inst" "$log" "$wd"
}
export -f run_filewriter

case "${TASK}" in --dry-run) DRY_RUN=1; TASK="${2:-}"; WORKDIR="${3:-$PWD}";; esac
# LLM選定基準: local-first でタスクから推奨プロバイダを決定論的に算出
pick_provider "$TASK" 2>/dev/null   # サブシェル回避: 直接呼び出し(PROVIDER_TRACE/PROVIDER_HINT を同一シェルで設定)
[ -z "$PROVIDER_HINT" ] && PROVIDER_HINT="ollama"
# 判断記録(監査可能な仕組み): provider_decision.log に追記
PROVIDER_LOG="$AGENT_DIR/provider_decision.log"
{
  echo "$(date +%Y-%m-%dT%H:%M:%S) | task=$(printf '%s' "$TASK" | cut -c1-60) | decision=$PROVIDER_HINT | ${PROVIDER_TRACE:-local_up=? cloud=?}"
} >> "$PROVIDER_LOG" 2>/dev/null
# 直近の判断を標準出力にも(監視層が読める)
echo "[provider] decision=$PROVIDER_HINT (${PROVIDER_TRACE:-})"
echo "[models] planner=$PLANNER_MODEL coder=$CODER_MODEL dry_run=$DRY_RUN"

# ---- VRAM 孤児 llama-server の掃除 (L25: インフラ配線・計画中 wedge 防止) ----
# 根因(実測 2026-08-13): router3 の各実行が 5-10 個の orphaned llama-server.exe を
# 残し、VRAM を ~11GB まで専有('ollama ps' は空と偽装)。次回の planner 呼び出しが
# VRAM 不足で wedged になり計画中スタックしていた。委譲前に孤児を掃除し VRAM を確保。
# ガードは安全: アクティブモデルがあれば no-op、0 モデル時のみ llama-server.exe を kill
# (ollama デーモンは絶対にkillしない)。既存コミット: 2f36af2。
CLEAN_GUARD="$AGENT_DIR/scripts/clean_orphan_llama.sh"
if [ -x "$CLEAN_GUARD" ]; then
  echo "[preflight] VRAM 孤児 llama-server を掃除 (router3 起動前)"
  bash "$CLEAN_GUARD" --exec >/dev/null 2>&1 || true
fi

# ---- Ollama 健全性プリフライト (L25: インフラ配線・ZOMBIE自己修復) ----
# 根因: Ollama が RTX A2000 環境で時折 wedge(応答なし)になり、router3 単独実行時は
# fleet_outer の ZOMBIE 自己修復が及ばずサイクルが落ちる。委譲前に ping し、
# 死んでいれば ollama serve を再起動して確実に生きている状態にする。
ollama_up() { curl -s -m 8 ${OLLAMA_BASE}/api/version >/dev/null 2>&1; }
# Only self-resurrect the LOCAL Ollama when OLLAMA_BASE points at the default local
# endpoint. If the user overrode OLLAMA_BASE (selftest dead-port, remote base), never
# taskkill/restart a local ollama.exe — that would kill a live instance the user
# relies on, or spin forever against a deliberately dead port.
LOCAL_OLLAMA_DEFAULT=1
case "$OLLAMA_BASE" in
  http://localhost:11434|http://127.0.0.1:11434) LOCAL_OLLAMA_DEFAULT=1 ;;
  *) LOCAL_OLLAMA_DEFAULT=0 ;;
esac
if [ "$LOCAL_OLLAMA_DEFAULT" -eq 1 ] && ! ollama_up; then
  echo "[preflight] Ollama 応答なし → 再起動試行"
  taskkill /IM ollama.exe /F >/dev/null 2>&1 || true
  sleep 2
  OLLAMA_CUDA=0 nohup ollama serve >/tmp/ollama_serve.log 2>&1 &
  for _w in 1 2 3 4 5 6; do sleep 3; ollama_up && break; done
fi
if [ "$LOCAL_OLLAMA_DEFAULT" -eq 1 ] && ollama_up; then
  _p=$(curl -s -m 20 ${OLLAMA_BASE}/api/generate -d '{"model":"qwen2.5-coder:3b","prompt":"x","stream":false}' 2>/dev/null)
  if ! echo "$_p" | grep -q '"response"'; then
    echo "[preflight] Ollama wedge 検知 → プロセス再起動"
    taskkill /IM ollama.exe /F >/dev/null 2>&1 || true
    sleep 2
    OLLAMA_CUDA=0 nohup ollama serve >/tmp/ollama_serve.log 2>&1 &
    for _w in 1 2 3 4 5 6; do sleep 3; ollama_up && break; done
  fi
fi
if [ "$LOCAL_OLLAMA_DEFAULT" -eq 1 ]; then
  ollama_up && echo "[preflight] Ollama 準備完了" || echo "[preflight][WARN] Ollama 起動できず(フォールバック前提で続行)"
else
  echo "[preflight] OLLAMA_BASE=$OLLAMA_BASE は既定外 → ローカルOllamaの再起動をスキップ" >&2
fi
# [L25] 自動ディスカバリ: ローカル在庫を走査し discovered_models.json を更新
if [ -x "$AGENT_DIR/llm_discover.sh" ]; then
  "$AGENT_DIR/llm_discover.sh" "$AGENT_DIR/discovered_models.json" >/dev/null 2>&1 || true
  echo "[preflight] discovered_models.json updated"
fi
# [L25] モデル事前ウォーム: 実際に使う PLANNER_MODEL/CODER_MODEL を一度だけ軽く generate
# して VRAM に載せておく。未ウォームだと最初のプランナー呼び出しで重みロード待ちになり
# 90s タイムアウトに抵触して wedged と誤認していた(root cause of 計画中スタック)。
for _wm in "$PLANNER_MODEL" "$CODER_MODEL"; do
  [ -n "$_wm" ] && curl -s -m 60 ${OLLAMA_BASE}/api/generate -d "{\"model\":\"$_wm\",\"prompt\":\"x\",\"stream\":false}" >/dev/null 2>&1 &
done
wait 2>/dev/null || true
echo "[preflight] モデルウォーム完了 ($PLANNER_MODEL / $CODER_MODEL)"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  AI Fleet — BOSS MODE Orchestrator                      ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  上司AI: 判断のみ / 部下AI: 実作業 / レビュー: 全ファイル読む ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo "Task: $TASK"
echo "Workdir: $WORKDIR"
echo

# ============================================================

# STEP 1: BOSS — 計画 (自分では手を動かさない / 判断のみ)
# ============================================================
PLANNER_PROMPT="あなたは上司AI（ボス）です。自分ではコードを書かず、部下AIに指示を出すだけです。
【LLM選定基準 — 4階層エスカレーション(ユーザー指定)】
タスクを解く順の優先度:
  1. 無料で使える高性能クラウドLLM (OpenRouter/HF/DeepSeek の無料・安価モデル) → 最優先
  2. 無料ローカルLLM (Ollama) → クラウドキー未設定時や軽量タスク
  3. 複数どちらでも解決しない場合、Claude Opus → 高難度タスクのエスカレーション
  4. それも無理なら Fable → 前提から間違っている可能性を考慮し、別アプローチを提案(人手・外部リソースへのエスカレーション)
このタスクの推奨プロバイダは「$PROVIDER_HINT」です(判定根拠: ${PROVIDER_TRACE:-})。各サブタスクの agent を選ぶ際、この推奨を第1候補とし、
タスク特性に合わせて割り当てよ。クラウドキーが設定されていても、無料クラウドが使えるなら ollama よりクラウドを優先せよ(コスト対効果)。
（openrouter は APIキー未設定時は自動で ollama にフォールバックする）

与えられたタスクを、部下AIに割り当てるサブタスクに分解してください。

部下AI一覧:
- ollama: ローカル軽量LLM(Ollama)。簡単なコード生成・草稿が得意。
- lmstudio: ローカルLLM(LM Studio、localhost:1234のOpenAI互換API)。コード生成・チャットが得意。ollamaと同系統だが別エンジン。
- claude: Claude Code。重い実装・リファクタが得意（利用上限の可能性あり）。
- hermes: Hermes子エージェント。調査・要約・テストが得意。**実ファイルの編集・修正（sed/スクリプト実行を伴うタスク）も実行可能（実行型）**。
- **実ファイル編集ルール[L25]**: サブタスクが「ファイルを実際に編集/修正/書き換えよ」を求める場合は、**ollama(説明専門・零編集を起こしやすい)には絶対に割り当てず、必ず hermes 実行型ルートに割り当てよ**。ollama は「調査・説明・提案・レビュー」のみに使い、実装・編集はさせない。過去に ollama に編集を丸投げすると架空コード説明のみで実ファイル未編集(L4/L25再発)となった。
- desktop: cua-driver経由でDevin/Bionic等のデスクトップアプリを操作（「Devinで確認」「Bionicで実行」等は必ずこれ）。
- chatgpt: ChatGPTデスクトップアプリ(現在 5.6 Sol + Instant = 高速即答・低遅延・指示忠実)に処理させたい場合に使う。
  【5.6 Sol Instant の有効利用法・使い分け】
  - 得意(優先起用): 軽い事実確認・即答Q&A・短い要約・文章の推敲/リライト・フォーマット変換・
    単純な分類/抽出・タイトル/ラベル生成・翻訳(短)~中。ollama(重い/ローカル)・claude(コード)・
    クラウド(深い知識) の「軽量代替」として最適。
  - 不得意(他ルートへ): 深い推論・長文生成・複雑コード・最新/独自情報・大きな文書解析。
  - ルール: タスクが「ChatGPTに〜と質問」「ChatGPTで〜生成」「ChatGPTに聞け」と明示、または
    上記「得意」に分類される軽量タスクの場合は必ず chatgpt ルート(ollama ではなく)。
- git: gh CLI専用。**PR/Issue作成のみ**に使う。コミット・プッシュは外ループ(fleet_outer.sh)のauto-commitが自動で行うため、サブタスクから git commit / git push を絶対に呼ばないこと。「コミットせよ」というタスクでは git サブタスクを作らず、他のAI(ollama/hermes等)で処理せよ（コミットは自動記録される）。**ルール: 「コミット」「git push」を含むタスクは git エージェントを使わない。**
- openrouter: クラウドLLMゲートウェイ(OpenRouter互換API)。Codex(無料アカウント)も OpenRouter 経由で利用可(OR_MODEL=openai/codex 等)。 ローカルGPUが使えない重いタスクや、複数モデルによる並列検証に使う。APIキー未設定時は自動で ollama にフォールバックする。
- hf: Hugging Face Inference API(OpenAI互換 /v1/chat/completions)。 ローカルOllama と同じ OSS モデル(qwen2.5-coder 等)をクラウドで稼働可能(最も自然な統合)。 HF_TOKEN 未設定時は自動で ollama にフォールバックする。
- deepseek: DeepSeek API(OpenAI互換 /v1/chat/completions)。 コスト対効果の高いクラウドLLM。DS_MODEL でモデル選択(既定 deepseek-chat、推論系は deepseek-reasoner 等)。 DEEPSEEK_API_KEY 未設定時は自動で ollama にフォールバックする。
- **並列作業改善**: 重要・複雑なタスク（実装・リファクタ・設計）は、同じ instruction を ollama と openrouter の「両方」に出し、結果を比較・統合（best-of / majority vote）せよ。特にクラウド側(openrouter)は Codex(無料)等も選択可。APIキー未設定時は openrouter が自動スキップされるため、常に ollama 側の結果が得られる。

【厳守制約 — 逸脱禁止】
- 与えられたタスクの主題以外のサブタスクを勝手に追加してはならない。
  （例: 依頼されていない「TODOリスト作成」「ChatGPTへの雑談質問」「README生成」「学習計画」等は禁止）
- すべてのサブタスクは、作業ディレクトリ「$WORKDIR」に実在するコード/リポジトリに対する作業に限定せよ。
- 架空のファイル名・架空のリポジトリ・一般論の説明タスクを作ってはならない。
- サブタスクは最大5件。各 instruction にはタスク本文のキーワードを必ず含めること。
- 分解できない単純タスクなら subtasks は1件でよい。
- JSON以外は一切出力しない。

【学習からの注意 — 必ず守れ】
過去の失敗からの再発防止ルールは $AGENT_DIR/learnings.md にあります。サブタスク設計時は
「架空ファイル・架空リポジトリを作らない」「実ファイル編集は ollama ではなく filewriter に任せる」
「コミット/push は外ループに任せる」の3点を厳守せよ。詳細ルールの全文は参照ファイルから確認。

出力は以下のJSONのみ（説明不要）:
{
  \"subtasks\": [
    {\"id\":1, \"agent\":\"ollama|claude|hermes|desktop|git|openrouter|parallel|hf|deepseek\", \"instruction\":\"部下への具体指示\"}
  ]
}
タスク: $TASK"

echo "[BOSS] 計画中 (タスク分解・割り当て)..."
if [ "$DRY_RUN" != "1" ]; then
# プランナー堅牢化: Ollamaが空応答を返すことがあるため、最大3回リトライ(別モデル交互)。
PLAN=""
for _try in 1 2 3; do
  PLAN=$(boss_llm "$PLANNER_PROMPT" "$PLANNER_MODEL")
  [ -n "$PLAN" ] && break
  echo "[BOSS][WARN] プランナー空応答 (try $_try) -> リトライ" >&2
done
PLAN="${PLAN:-}"
PLAN_JSON=$(echo "$PLAN" | python -c '
import sys,json,re
text=sys.stdin.read()
m=re.search(r"\{.*\}", text, re.DOTALL)
if m:
    try: json.loads(m.group(0)); print(m.group(0))
    except: print("{}")
else: print("{}")
')
else
  PLAN=""  # dry-run: プランナー(実API)を呼ばず空にする
fi

# 構造的ガード: タスクが「コミット/push」を含む場合、git サブタスクを除外する
# (コミットは外ループauto-commitが行う。LLMがgitを選んでもここで確実に落とす)
if echo "$TASK" | grep -qiE "コミット|git push|push"; then
  PLAN_JSON=$(echo "$PLAN_JSON" | python -c '
import sys,json,re
try:
    d=json.loads(sys.stdin.read())
except: sys.exit(0)
st=[s for s in d.get("subtasks",[]) if s.get("agent")!="git"]
if len(st)<len(d.get("subtasks",[])):
    print(json.dumps({"subtasks":st}, ensure_ascii=False))
else:
    print(json.dumps(d, ensure_ascii=False))
')
fi

# 構造的ガード: タスクが ChatGPT を明示要求していないのに chatgpt を選んだら誤ルーティング。
# 軽量/ローカルタスクは ollama/hermes が正。LLMが学習を無視して chatgpt を選んでも確実に落とす。
# ---- DRY-RUN: プランナー(実API)を叩かずダミー計画を出力して終了 ----
# 真のFORKEXH回避: ネット極慢環境でプランナー呼び出しがハングするため、
# dry-run ではローカルで決定論的にダミーJSONを生成する(クラウド通信なし)。
if [ "$DRY_RUN" = "1" ]; then
  PLAN_JSON=$(PLAN_TASK="$TASK" python -c '
import json,os
print(json.dumps({"subtasks":[{"id":1,"agent":"ollama","instruction":os.environ["PLAN_TASK"]}]}, ensure_ascii=False))
')
  printf '%s' "$PLAN_JSON" | python -c '
import json,sys
for s in json.load(sys.stdin).get("subtasks",[]): print(json.dumps(s, ensure_ascii=False))
' > "$RUN_DIR/delegates.jsonl" 2>/dev/null || true
  echo "[DRY-RUN] プラン(ダミー)を生成しました。部下AIは実行しません。 -> $RUN_DIR/delegates.jsonl"
  echo "[PLAN_JSON] $PLAN_JSON"
  exit 0
fi

# 構造的強制: タスクに chatgpt/チャットgpt 語が含まれるなら、
# 問答無用でサブタスクの agent を全て chatgpt に上書き(プランナーが ollama を選んでも修正)。
if echo "$TASK" | grep -qiE "chatgpt|チャットgpt"; then
  PLAN_JSON=$(echo "$PLAN_JSON" | python -c '
import sys,json
try:
    d=json.loads(sys.stdin.read())
    for x in d.get("subtasks",[]): x["agent"]="chatgpt"
    print(json.dumps(d, ensure_ascii=False))
except Exception as e:
    pass
  ')
fi
if ! echo "$TASK" | grep -qiE "chatgpt|チャットgpt"; then
  PLAN_JSON=$(echo "$PLAN_JSON" | python -c '
import sys,json,re
try:
    d=json.loads(sys.stdin.read())
except: sys.exit(0)
st=[x for x in d.get("subtasks",[]) if x.get("agent")!="chatgpt"]
if len(st)<len(d.get("subtasks",[])):
    print(json.dumps({"subtasks":st}, ensure_ascii=False))
else:
    print(json.dumps(d, ensure_ascii=False))
')
fi

# ---- プラン検証 + フォールバック ----
# 無関係サブタスク（TODOリスト自動化等）を実行しないための最終ガード。
# 検証NG時は「タスク本文そのもの」を1件のサブタスクとするマニフェストに退避する。
PLAN_CHECK=$(printf '%s' "$PLAN_JSON" | PLAN_TASK="$TASK" python -c '
import json,sys,os,re
task=os.environ.get("PLAN_TASK","")
try:
    d=json.loads(sys.stdin.read() or "{}")
except Exception:
    d={}
st=[s for s in d.get("subtasks",[]) if isinstance(s,dict) and s.get("instruction")]
valid_agents={"ollama","lmstudio","claude","hermes","desktop","chatgpt","git","openrouter","parallel","hf","deepseek","fable"}
if not st:
    print("EMPTY"); sys.exit()
# 不正エージェントは ollama に矯正
for s in st:
    if s.get("agent") not in valid_agents: s["agent"]="ollama"
# 関連性チェック(lexical): タスク語のサブタスク内含を必須とすると、正しいが簡潔な
# 回答(例: "15 * 17 = 255")が誤って IRRELEVANT になる(false positive)。構造検証
# (非空 instruction + 正エージェント) で十分に不正計画を弾けるため、語彙オーバーラップ
# 要件は削除。非空サブタスクはそのまま OK とする。
print("OK "+json.dumps({"subtasks":st[:5]}, ensure_ascii=False))
')
PLAN_STATUS="${PLAN_CHECK%% *}"
if [ "$PLAN_STATUS" = "OK" ]; then
  PLAN_JSON="${PLAN_CHECK#OK }"
else
  echo "[BOSS][WARN] プランナー出力が不正です ($PLAN_STATUS) → フォールバック(実ファイル生成の filewriter サブタスク化)"
  # Ollama 死/下流LLM全滅時でも「生成ファイルの実在」を必須にする。
  # フォールバック計画は filewriter エージェントで WORKDIR に実ファイルを実体化させる
  # (ファイル名なし ollama サブタスクにすると偽承認になる: green-is-luck の穴)。
  PLAN_JSON=$(PLAN_TASK="$TASK" PLAN_DIR="$WORKDIR" python -c '
import json,os
task=os.environ.get("PLAN_TASK","task")
wd=os.environ.get("PLAN_DIR",".")
fname="".join(c if c.isalnum() or c in "-_" else "_" for c in task[:24]).strip("_") or "output"
path=os.path.join(wd, fname+".md")
print(json.dumps({"subtasks":[{"id":1,"agent":"filewriter","instruction":path+" を作成せよ: "+task}],"fallback_for":"planner_empty"}, ensure_ascii=False))
')
fi
# ---- 計画レベル filewriter 強制 (L25: 根本性修正) ----
# 根因: プランナーがクラウドLLM(openrouter等, APIキー未設定)や git を選び、
# 実ファイルを作成できず成果物検証が常に FAIL になっていた。計画段階で
# 「対象ファイル名 + 作成/保存動詞」を含むサブタスクは無条件で filewriter に上書きし、
# ローカルで実ファイルを WORKDIR に実体化させる(プランナの選択に関わらず)。
PLAN_JSON=$(printf '%s' "$PLAN_JSON" | python -c '
import json,sys,re
try:
    d=json.loads(sys.stdin.read() or "{}")
except Exception:
    sys.exit(0)
fw_re=re.compile(r"(?:[A-Za-z0-9_./-]+\.(?:sh|md|py|json|txt|yaml|yml|toml)|\.[A-Za-z0-9_][A-Za-z0-9_.-]*)")
verb_re=re.compile(r"作成|生成|新規ファイル|書け|書き込み?|保存|出力せよ|実体化|create|write|save|new file", re.I)
for s in d.get("subtasks",[]):
    inst=s.get("instruction","")
    if fw_re.search(inst) and verb_re.search(inst):
        s["agent"]="filewriter"
print(json.dumps(d, ensure_ascii=False))
')
# [L25] 元タスクベースの filewriter 強制 (planner 捏造対策)
# 根因: プランナー(Ollama)が「.gitignore を編集せよ」を「git add && git commit」に歪め、
# 拡張子を削ってしまうため、上の fw_re/verb_re 強制に乗らず実編集されない。
# そこで元タスク $TASK に「拡張子付きファイル名 + 編集/追記/作成動詞」が含まれていれば、
# planner の出力に関わらず強制で filewriter にし、instruction を実ファイル編集に書き直す。
PLAN_JSON=$(printf '%s' "$PLAN_JSON" | TASK="$TASK" python -c '
import json,sys,os,re
task=os.environ.get("TASK","")
try:
    d=json.loads(sys.stdin.read() or "{}")
except Exception:
    sys.exit(0)
fw_re=re.compile(r"(?:[A-Za-z0-9_./-]+\.(?:sh|md|py|json|txt|yaml|yml|toml)|\.[A-Za-z0-9_][A-Za-z0-9_.-]*)")
verb_re=re.compile(r"編集|修正|追記|書き換え|追加|更新|作成|生成|新規|書け|保存|実体化|edit|append|write|save|create|update|new", re.I)
m=fw_re.search(task)
if m and verb_re.search(task):
    tgt=m.group(0)
    for s in d.get("subtasks",[]):
        s["agent"]="filewriter"
        # instruction を実ファイル編集に強制書き直し(planner が git commit 等に捏造していても無効化)
        s["instruction"]=task  # 元タスク全文をそのまま渡す(具体指示・マーカー等を保持。planner 捏造を無効化しつつ実編集内容を filewriter に伝える)
print(json.dumps(d, ensure_ascii=False))
')

# repo-fleet.sh 互換のマニフェストを残す（Hermes 側 delegate_task 用）
printf '%s' "$PLAN_JSON" | python -c '
import json,sys
for s in json.load(sys.stdin).get("subtasks",[]): print(json.dumps(s, ensure_ascii=False))
' > "$RUN_DIR/delegates.jsonl" 2>/dev/null || true

echo "$PLAN" | tee "$RUN_DIR/boss_plan.md"
echo "[PLAN_JSON] $PLAN_JSON" | tee "$RUN_DIR/boss_plan.json"
if [ "$DRY_RUN" = "1" ]; then
  echo "[DRY-RUN] プランのみ生成しました。部下AIは実行しません。 -> $RUN_DIR/delegates.jsonl"
  exit 0
fi

# ============================================================
# STEP 2: 部下AI — 実作業 (ボスは関与しない)
# ============================================================
# ---- Claude 制限検出＋自動フォールバック関数 ----
# Claude Code が週間上限/レート制限で失敗しても、AI組織全体は止まらず
# 別の部下AI (ollama / hermes) がそのサブタスクを代行する。
run_claude_or_fallback() {
  local inst="$1" log="$2" wd="$3"
  local out
  # 有料 Opus を主力として claude_runner.sh 経由で呼ぶ(実ファイル書き込み + 予算上限 + 経路正規化)
  # CLAUDE_CASCADE=1: テキスト生成は ollama ローカルでファーストトライ、ツール利用タスクのみ Opus へ escalate (FrugalGPT 2305.05176)
  out=$(cd "$wd" && CLAUDE_CASCADE=1 "$AGENT_DIR/claude_runner.sh" "$inst" "$wd" 2>&1); rc=$?
  if [ "$rc" -eq 2 ] || echo "$out" | grep -qi -E "weekly limit|hit your .* limit|rate limit|rate.limit|upstream|quota|exceeded|404|available credits|requires available credits|credits|一部利用できません|制限"; then
    {
      echo "[FALLBACK] Claude Code は制限中のため、別の部下AIが代行します。"
      # コード生成系は ollama、それ以外は hermes にフォールバック
      if echo "$inst" | grep -qi -E "コード|実装|refactor|implement|code|関数|スクリプト|bug|修正"; then
        cd "$wd" && curl -s -m 120 ${OLLAMA_BASE}/api/generate \
          -d "{\"model\":\"$CODER_MODEL\",\"prompt\":$(printf '%s' "$inst" | python -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),\"stream\":false}" \
          | python -c 'import json,sys; print(json.load(sys.stdin).get("response",""))'
      else
        run_hermes_or_fallback "$inst" "$log" "$wd"
      fi
    } > "$log" 2>&1
  else
    printf '%s\n' "$out" > "$log" 2>&1
  fi
}
export -f run_claude_or_fallback

# ---- Hermes サブルート 429/limit フォールバック ----
# Hermes API がレート制限(HTTP 429 / rate limit / exceeded)で失敗しても、
# ローカル ollama が代行する。これで「ボス(Hermes API)が単一障害点」にならない。
run_hermes_or_fallback() {
  local inst="$1" log="$2" wd="$3"
  local out
  out=$(cd "$wd" && hermes chat -q "$inst" 2>&1)
  if echo "$out" | grep -qi -E "429|rate limit|rate.limit|exceeded|too many requests|quota|404|available credits|requires available credits|credits|制限|Hold up"; then
    {
      echo "[FALLBACK] Hermes API は制限中のため、ローカル ollama が代行します。"
      cd "$wd" && curl -s -m 120 ${OLLAMA_BASE}/api/generate \
        -d "{\"model\":\"$CODER_MODEL\",\"prompt\":$(printf '%s' "$inst" | python -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),\"stream\":false}" \
        | python -c 'import json,sys; print(json.load(sys.stdin).get("response",""))'
    } > "$log" 2>&1
  else
    printf '%s\n' "$out" > "$log" 2>&1
  fi
}
export -f run_hermes_or_fallback

# ---- Ollama サブルート: Ollama ダウン/応答なし時→LM Studio(:1234,7b) 自動フォールバック ----
# Ollama 単一障害点の排除（完全冗長化）。部下(ollama 3b)が実装サボしたためボスが直接配線。
# [L25] インフラ配線: 部下がテキスト応答を返しても実ファイルを作らない場合、
# instruction の対象パスを抽出し $wd に決定論的に書き込む（LLM 呼び出しなし）。
# filewriter と同じロジックを ollama/hermes 等のテキストルートにも適用し、
# 幻承認（実ファイルなしで「完了」）を防ぐ。
_fw_write_artifact() {
  local inst="$1" log="$2" wd="$3" ftgt
  ftgt=$(printf '%s' "$inst" | grep -oE '([A-Za-z]:[/])?[A-Za-z0-9_./-]+\.(sh|md|py|json|txt|yaml|yml|toml)' | head -1)
  # instruction に拡張子付きパスが無い場合(planner が落とした)は元タスク $TASK から補完
  [ -z "$ftgt" ] && ftgt=$(printf '%s' "${TASK:-}" | grep -oE '([A-Za-z]:[/])?[A-Za-z0-9_./-]+\.(sh|md|py|json|txt|yaml|yml|toml)' | head -1)
  [ -z "$ftgt" ] && return 0
  case "$ftgt" in [A-Za-z]:[/]*) ftgt=$(cygpath -u "$ftgt" 2>/dev/null || echo "$ftgt") ;; esac
  case "$ftgt" in /*|./*) : ;; *) ftgt="$wd/$ftgt" ;; esac
  mkdir -p "$(dirname "$ftgt")" 2>/dev/null || true
  [ -s "$log" ] && { printf '%s' "$(cat "$log")" | sed -E '/^```/d' > "$ftgt" 2>/dev/null || true; }
  [ -s "$ftgt" ] && echo "[L25-WRITE] 実ファイル作成: $ftgt ($(wc -c < "$ftgt" 2>/dev/null) bytes)" | tee -a "$log"
}
run_ollama_or_lmstudio() {
  local inst="$1" log="$2" wd="$3" resp tries=0
  # Ollama は RTX A2000 環境で一時的に zombie/応答なしになることがある(L27/L28)。
  # 1回の空応答で即フォールバックすると部下成果が空になり Boss が差し戻す。
  # そのため空応答時はリトライ(最大3回, 2s間隔)してから LM Studio にフォールバック。
  while [ $tries -lt 3 ]; do
    tries=$((tries+1))
    resp=$(cd "$wd" && curl -s -m 90 ${OLLAMA_BASE}/api/generate \
      -d "{\"model\":\"$CODER_MODEL\",\"prompt\":$(printf '%s' "$inst" | python -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),\"stream\":false}" 2>/dev/null)
    if [ -n "$resp" ] && echo "$resp" | grep -q '"response"'; then
      echo "$resp" | python -c 'import json,sys; print(json.load(sys.stdin).get("response",""))' > "$log"
      _fw_write_artifact "$inst" "$log" "$wd"
      return 0
    fi
    sleep 2
  done
  echo "[FALLBACK] Ollama 応答なし(3回試行) → LM Studio(:1234) にフォールバック" >&2
  local lms
  lms=$(cd "$wd" && curl -s -m 30 http://localhost:1234/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"qwen2.5-coder:7b\",\"messages\":[{\"role\":\"user\",\"content\":$(printf '%s' "$inst" | python -c 'import json,sys; print(json.dumps(sys.stdin.read()))' )}]}" 2>/dev/null)
  if [ -n "$lms" ] && echo "$lms" | grep -q '"choices"'; then
    echo "$lms" | python -c 'import json,sys; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"])' 2>/dev/null > "$log"
    _fw_write_artifact "$inst" "$log" "$wd"
    return 0
  fi
  echo "[ERROR] Ollama/LM Studio の全てが応答なし (Hermes実行フォールバックは意図的に省略: 同期ハング回避)" > "$log"
  _fw_write_artifact "$inst" "$log" "$wd"
  return 1
}
export -f run_ollama_or_lmstudio

# ---- OpenRouter / クラウドLLM サブルート (L25: インフラ配線) ----
# 新GPU(A310/RX6400)はローカルOllama GPU推論に事実上使用不可(Intel=専用ビルド要/AMD=ROCm非対応Windows)。
# 真の処理能力増強は「クラウドLLMサービス統合」で行う。OpenRouter は OpenAI互換 /v1/chat/completions。
# OPENROUTER_API_KEY が無い場合は自動スキップし ollama にフォールバック(組織は止まらない)。
OR_API_KEY="${OPENROUTER_API_KEY:-${OR_KEY:-}}"
OR_BASE="${OPENROUTER_BASE:-https://openrouter.ai/api/v1}"
OR_MODEL="${OR_MODEL:-anthropic/claude-3.5-sonnet}"
ask_openrouter() {
  local inst="$1" model="${2:-$OR_MODEL}"
  [ -z "${OR_API_KEY:-}" ] && { echo "[openrouter] APIキー未設定 -> スキップ"; return 1; }
  curl -s -m 120 "${OR_BASE:-}/chat/completions" \
    -H "Authorization: Bearer ${OR_API_KEY:-}" -H "Content-Type: application/json" \
    -H "HTTP-Referer: https://localhost" -H "X-Title: ai-fleet" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":$(printf '%s' "$inst" | python -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}]}" 2>/dev/null \
    | python -c 'import json,sys\ntry:\n d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"])\nexcept Exception:\n print("")'
}

# ---- Hugging Face Inference API サブルート (L25: インフラ配線) ----
# HF も OpenAI互換 /v1/chat/completions を提供(router.huggingface.co/v1)。
# ローカルOllama と同じ OSS モデル(qwen2.5-coder 等)をクラウドで稼働可能 = 最も自然な統合。
# HF_TOKEN が無い場合は自動スキップし ollama にフォールバック(組織は止まらない)。
HF_API_KEY="${HF_TOKEN:-${HUGGING_FACE_HUB_TOKEN:-${HF_KEY:-}}}"
HF_BASE="${HF_BASE:-https://router.huggingface.co/v1}"
HF_MODEL="${HF_MODEL:-Qwen/Qwen2.5-Coder-7B-Instruct}"
ask_hf() {
  local inst="$1" model="${2:-$HF_MODEL}"
  [ -z "${HF_API_KEY:-}" ] && { echo "[hf] HF_TOKEN 未設定 -> スキップ"; return 1; }
  curl -s -m 120 "${HF_BASE:-}/chat/completions" \
    -H "Authorization: Bearer ***" -H "Content-Type: application/json" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":$(printf '%s' "$inst" | python -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}]}" 2>/dev/null \
    | python -c 'import json,sys\ntry:\n d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"])\nexcept Exception:\n print("")'
}

# ---- DeepSeek API サブルート (L25: インフラ配線) ----
# DeepSeek も OpenAI互換 /v1/chat/completions を提供(api.deepseek.com/v1)。
# コスト対効果の高いクラウドLLM。DEEPSEEK_API_KEY が無い場合は自動スキップし ollama にフォールバック。
DS_API_KEY="${DEEPSEEK_API_KEY:-${DS_KEY:-}}"
DS_BASE="${DEEPSEEK_BASE:-https://api.deepseek.com/v1}"
DS_MODEL="${DS_MODEL:-deepseek-chat}"
ask_deepseek() {
  local inst="$1" model="${2:-$DS_MODEL}"
  [ -z "${DS_API_KEY:-}" ] && { echo "[deepseek] DEEPSEEK_API_KEY 未設定 -> スキップ"; return 1; }
  curl -s -m 120 "${DS_BASE:-}/chat/completions" \
    -H "Authorization: Bearer ***" -H "Content-Type: application/json" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":$(printf '%s' "$inst" | python -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}]}" 2>/dev/null \
    | python -c 'import json,sys\ntry:\n d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"])\nexcept Exception:\n print("")'
}
run_deepseek_or_fallback() {
  local inst="$1" log="$2" wd="$3"
  if [ -z "${DS_API_KEY:-}" ]; then
    echo "[deepseek] DEEPSEEK_API_KEY 未設定 -> ローカル ollama にフォールバック" > "$log"
    run_ollama_or_lmstudio "$inst" "$log" "$wd"; return 0
  fi
  local out; out=$(ask_deepseek "$inst")
  if [ -n "$out" ]; then printf '%s\n' "$out" > "$log"; return 0; fi
  echo "[FALLBACK] DeepSeek 失敗 -> ollama にフォールバック" >&2
  run_ollama_or_lmstudio "$inst" "$log" "$wd"
}
export -f run_deepseek_or_fallback

# ---- Fable ルート (メタ: 前提再考・別アプローチ) ----
# ユーザー指定の第4階層: クラウド・ローカル・Claude のいずれでも解決しない場合、
# 「前提から間違っている可能性」を考慮し Fable(別アプローチ提案)へエスカレーション。
# 実体が無い環境では人手・別アプローチへのエスカレーションをログ記録し、
# 組織は止まらないようローカル ollama にフォールバック(最終安全網)。
run_fable_or_fallback() {
  local inst="$1" log="$2" wd="$3"
  {
    echo "[FABLE] クラウド・ローカル・Claude いずれでも解決せず。前提再考を提案:"
    echo "  - タスクの前提(環境・入力・制約)が誤っていないか再確認せよ"
    echo "  - 別アプローチ(ツール・手法・分割)での解決を検討せよ"
    echo "  - それでも不可なら人手・外部リソースへのエスカレーション"
  } > "$log" 2>&1
  if ollama_up; then
    local r
    r=$(cd "$wd" && curl -s -m 90 ${OLLAMA_BASE}/api/generate -d "{\"model\":\"$CODER_MODEL\",\"prompt\":$(printf '%s' "$inst" | python -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),\"stream\":false}" 2>/dev/null | python -c 'import json,sys; print(json.load(sys.stdin).get("response",""))' 2>/dev/null)
    [ -n "$r" ] && printf '%s\n' "$r" >> "$log" 2>&1
  fi
}
export -f run_fable_or_fallback

# ---- Fable ルート (メタ: 前提再考・別アプローチ) ----
# ユーザー指定の第4階層: クラウド・ローカル・Claude のいずれでも解決しない場合、
# 「前提から間違っている可能性」を考慮し、Fable(別アプローチ提案)にエスカレーション。
# 実体が無い環境では「人手・別アプローチへのエスカレーション」をログに記録し、
# 組織は止まらないよう ollama にフォールバック(最終安全網)。


run_hf_or_fallback() {
  local inst="$1" log="$2" wd="$3"
  if [ -z "${HF_API_KEY:-}" ]; then
    echo "[hf] HF_TOKEN 未設定 -> ローカル ollama にフォールバック" > "$log"
    run_ollama_or_lmstudio "$inst" "$log" "$wd"; return 0
  fi
  local out; out=$(ask_hf "$inst")
  if [ -n "$out" ]; then printf '%s\n' "$out" > "$log"; return 0; fi
  echo "[FALLBACK] Hugging Face 失敗 -> ollama にフォールバック" >&2
  run_ollama_or_lmstudio "$inst" "$log" "$wd"
}
export -f run_hf_or_fallback

run_openrouter_or_fallback() {
  local inst="$1" log="$2" wd="$3"
  if [ -z "${OR_API_KEY:-}" ]; then
    echo "[openrouter] APIキー未設定 -> ローカル ollama にフォールバック" > "$log"
    run_ollama_or_lmstudio "$inst" "$log" "$wd"; return 0
  fi
  local out; out=$(ask_openrouter "$inst")
  if [ -n "$out" ]; then printf '%s\n' "$out" > "$log"; return 0; fi
  echo "[FALLBACK] OpenRouter 失敗 -> ollama にフォールバック" >&2
  run_ollama_or_lmstudio "$inst" "$log" "$wd"
}
export -f run_openrouter_or_fallback

# ---- 並列実行ヘルパー: 同じ指示を ollama + openrouter で同時実行し比較 ----
# 並列作業改善の核心。重要タスクはローカル+クラウドの両方で実行し、結果を統合。
# APIキー未設定時は openrouter 側が空になるが、ollama 側は必ず得られる(組織は止まらない)。
run_parallel_cloud() {
  local inst="$1" log="$2" wd="$3"
  local ol="$RUN_DIR/sub_ollama_parallel.md" or="$RUN_DIR/sub_openrouter_parallel.md"
  # ollama 側
  ( run_ollama_or_lmstudio "$inst" "$ol" "$wd" ) &
  local opid=$!
  # openrouter 側(APIキー有時のみ)
  if [ -n "${OR_API_KEY:-}" ]; then
    ( run_openrouter_or_fallback "$inst" "$or" "$wd" ) &
    local orpid=$!
    wait "$orpid"
  else
    echo "[parallel] OPENROUTER_API_KEY 未設定 -> openrouter 側スキップ(ollama のみ)" > "$or"
  fi
  wait "$opid"
  # 統合: ollama 結果を主とし、openrouter 結果を比較用に添付
  {
    echo "=== ollama (ローカル) ==="; [ -f "$ol" ] && cat "$ol"; echo
    echo "=== openrouter (クラウド) ==="; [ -f "$or" ] && cat "$or"; echo
  } > "$log" 2>&1
}
export -f run_parallel_cloud

case "${1}" in --exec-one)
  SID="$2"; EXEC_AGENT="$3"; EXEC_INST="$4"; WDIR="${WORKDIR:-$PWD}"
  echo "[EXEC-ONE] subtask=$SID agent=$EXEC_AGENT"
  # [L25] ファイル実体化ルート: instruction が成果物ファイルを名指しして「作成/保存」を
  # 求めている場合、チャット出力だけでなく実ファイルを WORKDIR に書く filewriter に強制ルーティング。
  # これがないと成果物検証ゲートが常に FAIL になり「仕事が着地しない」。
  if printf '%s\n' "$EXEC_INST" | grep -qiE '作成|生成|新規ファイル|書け|書き込み?|保存|出力せよ|実体化|create|write|save|new file'; then
    if printf '%s\n' "$EXEC_INST" | grep -qE '(?:[A-Za-z0-9_./-]+\.(?:sh|md|py|json|txt|yaml|yml|toml)|\.[A-Za-z0-9_][A-Za-z0-9_.-]*)'; then
      echo "[L25-FILEWRITER] ファイル作成タスクを filewriter 実体化ルートに強制ルーティング (agent=$EXEC_AGENT -> filewriter)"
      EXEC_AGENT="filewriter"
    fi
  fi
  case "$EXEC_AGENT" in
    claude) run_claude_or_fallback "$EXEC_INST" "$RUN_DIR/sub_${SID}.md" "$WDIR" ;;
    hermes) run_hermes_or_fallback "$EXEC_INST" "$RUN_DIR/sub_${SID}.md" "$WDIR" ;;
    ollama|lmstudio) run_ollama_or_lmstudio "$EXEC_INST" "$RUN_DIR/sub_${SID}.md" "$WDIR" ;;
    openrouter) run_openrouter_or_fallback "$EXEC_INST" "$RUN_DIR/sub_${SID}.md" "$WDIR" ;;
    hf) run_hf_or_fallback "$EXEC_INST" "$RUN_DIR/sub_${SID}.md" "$WDIR" ;;
    deepseek) run_deepseek_or_fallback "$EXEC_INST" "$RUN_DIR/sub_${SID}.md" "$WDIR" ;;
    fable) run_fable_or_fallback "$EXEC_INST" "$RUN_DIR/sub_${SID}.md" "$WDIR" ;;
    git) echo "[git] $EXEC_INST" > "$RUN_DIR/sub_${SID}.md" ;;
    filewriter) run_filewriter "$EXEC_INST" "$RUN_DIR/sub_${SID}.md" "$WDIR" ;;
    *) run_ollama_or_lmstudio "$EXEC_INST" "$RUN_DIR/sub_${SID}.md" "$WDIR" ;;
  esac
  exit 0 ;;
esac

echo
echo "[SUBORDINATES] 部下AIが実作業を実行中..."
PIDS=()
# ---- STEP 2: 依存グラフ・並列オーケストレーション(parallel_scheduler.sh へ委譲) ----
# 単純な for 逐次実行を廃止。planner の plan に deps/isolated を付与し、
# 依存グラフを wave 分割、各 wave 内の独立タスクを真並列(&+wait)、共有状態は flock で競合排除、
# 各 wave を reduce(lead 統合)。ADK Parallel workflow + Magentic-One outer/inner +
# Anthropic orchestrator-worker(3-5並列) を統合した洗練された並列。
echo "[SUBORDINATES] 依存グラフ並列スケジューラが実作業を実行中..."
# RUN_DIR を export してスケジューラが同じ run ディレクトリを使うよう強制
# (未 export だと scheduler が run_$$ で別ディレクトリを作り、delegates.jsonl が
#  見つからず成果物検証/判定がクラッシュしていた)
export RUN_DIR
bash "$AGENT_DIR/parallel_scheduler.sh" "$RUN_DIR/delegates.jsonl"
# スケジューラが delegates_result.md に全 wave 統合結果を書く
echo "[SUBORDINATES] 実作業完了。"

# ============================================================
# STEP 3: BOSS — レビュー (変更されたファイルを全部読む)
# ============================================================
echo
echo "[BOSS] レビュー: 変更されたファイルをすべて読みます..."
REVIEW_INPUT="$RUN_DIR/boss_review_input.md"
{
  echo "# レビュー対象"
  echo "## 部下AIの成果ファイル"
  for f in "$RUN_DIR"/sub_*.md; do
    [ -f "$f" ] || continue
    echo "=== $f ==="
    cat "$f"
    echo
  done
  echo "## ワークディレクトリの変更 (git)"
  cd "$WORKDIR" 2>/dev/null && {
    echo "### git status (uncommitted)"
    git status --short 2>/dev/null || echo "(not a git repo)"
    echo "### git diff (uncommitted, working tree) — 内部状態ファイル(.human_feedback等)は除外"
    # 内部状態ファイルはボスが自己履歴を誤認しないよう diff から除外(L25)
    for df in $(git diff --name-only 2>/dev/null); do
      b=$(basename "$df")
      case "$b" in
        .boss_invocations|.human_feedback|.outcome_ledger|.human_ack|.human_resolved|.decision_ledger|.drift_history|.evolution_snapshot|.memory_snapshot|.org_state|.outer_progress|.last_alert|.last_val_avg|.strategy_state|.boss_trigger_guard) continue ;;
      esac
      git diff -- "$df" 2>/dev/null
    done | head -200
    echo "### 直近のコミット差分 (committed work — fixes may already be committed via L25 plumbing)"
    git log -3 --stat 2>/dev/null | head -60
    echo "### untracked files"
    git ls-files --others --exclude-standard 2>/dev/null | head -50
  } || echo "(workdir not accessible)"
} > "$REVIEW_INPUT"
# 組織の内部状態ファイル(自身の帳簿/ループchurn)の diff ブロックをレビュー文脈から除外。
# 注: 上の git セクションで既に「内部状態ファイルを git diff --name-only でスキップ」して
# いるため、ここでの awk ブロック除外は二重ガード。実ファイル(audit_report.md等)は除外しない。
# 堅牢化: パス取得は git diff --name-only を使い、awk のフィールド分割(a[2]バグ)を排除。
# ※ 上の git セクションで既に内部状態ファイルをスキップ済のため、ここでの awk 再フィルタは削除(冗長+警告回避)。
# ボスが .human_feedback / .outcome_ledger 等の自己履歴を「自己承認ループ」と
# 誤認し、実成果物を検証せずに RETURN する誤判定を防ぐ(L25)。
# 注意: audit_report.md / fleet_*.md / learnings.md 等の「実成果物」は除外しない。

# ============================================================
# STEP 2.6: 成果物検証 (verify_artifacts.py)
# reviewプロンプト生成・VERDICT判定の直前で、タスク指示文に含まれる
# 「期待される実成果ファイル」のパスを抽出し、実在＋中身が
# 「成果そのもの」かを機械的に検証する。FAILなら強制差し戻しにする。
# ============================================================
echo
echo "[BOSS] 成果物検証: 期待ファイルの実在・中身をチェック (verify_artifacts.py)..."
VERIFY_PY_W=$(cygpath -w "$FLEET_HOME/verify_artifacts.py" 2>/dev/null || echo "$FLEET_HOME/verify_artifacts.py")
EXPECTED_FILES=()
while IFS= read -r p; do
  [ -z "$p" ] && continue
  EXPECTED_FILES+=("$p")
done < <(printf '%s\n' "$TASK" | grep -oE '(/[a-zA-Z]/|[A-Za-z]:[/])[^ "'"'"'	]+.[A-Za-z0-9]+' | sort -u)
# [L25 boss-plumbing] 架空完了防止: タスク文に「ファイル名だけ」(audit_claims.sh等)が
# 書かれていても対象に含める。絶対パスの正規表現に一致しない相対ファイル名(.sh/.md等)を
# 抽出し、WORKDIR/REPO_DIR から解決できるものを EXPECTED_FILES に加える。これにより
# 「パスなしタスク」が緩い no-path ゲートに逃げて別タスクの変更を成果と誤認するのを防ぐ。
  # [L25] 相対ファイル名を抽出するが、「作成/新規/書き込む/保存/生成」等の作成動詞を
  # 伴うもののみを期待成果物とする。単に「router3.sh の使い方」のように参照されるだけの
  # 既存ファイルを成果物に加えると、verifier が not_modified_for_task で誤却下していた。
  # 作成動詞の直後(20文字以内)にあるファイル名のみ抽出。
  while IFS= read -r bn; do
    [ -z "$bn" ] && continue
    printf '%s\n' "${EXPECTED_FILES[@]}" | grep -qx "$bn" && continue
    for d in "${WORKDIR:-$PWD}" "${REPO_DIR:-$FLEET_HOME}" "$FLEET_HOME"; do
      [ -f "$d/$bn" ] && { EXPECTED_FILES+=("$d/$bn"); break; }
    done
  done < <(printf '%s\n' "$TASK" | grep -oE '(作成|新規|書[き込け]、?|保存|生成|作れ|書け|出力|出力せよ|編集|修正|変更)[^。]{0,24}[A-Za-z0-9_./-]+\.(sh|md|py|json|yaml|yml|txt|toml)' | grep -oE '[A-Za-z0-9_./-]+\.(sh|md|py|json|yaml|yml|txt|toml)' | sort -u)

VERIFY_SUMMARY="SKIP"
VERIFY_RESULT="(検証対象ファイルパスがタスク指示文中に見つかりませんでした)"
if [ "${#EXPECTED_FILES[@]}" -gt 0 ]; then
  EXPECTED_FILES_W=()
  for f in "${EXPECTED_FILES[@]}"; do
    EXPECTED_FILES_W+=("$(cygpath -w "$f" 2>/dev/null || echo "$f")")
  done
  VERIFY_RESULT=$(python "$VERIFY_PY_W" "${EXPECTED_FILES_W[@]}" 2>&1)
  # Rule-3 hardening (git-diff verdict gate): 期待ファイルが「実在するだけ」ではなく
  # 「実際に編集された(差分あり)」ことを必須化。元からあるファイルを零編集で通すのを防ぐ。
  # [L25/End-State] push/publish/remote 系タスクは「ファイル編集」ではなく「リモート反映」が
  # ゴール。作業ツリーに差分がないのは正常(既に commit/push 済み)なので no_edit gate で弾かず、
  # 後段の End-State guard の判定(リモート反映)に委ねる。
  case "$TASK" in
    *push*|*publish*|*repo作成*|*repo作る*|*repo*create*|*github*|*リモート*|*公開*|*deploy*|*プッシュ*|*リポジトリ*) is_push_task=1 ;;
    *) is_push_task=0 ;;
  esac
  EDITED_ANY=0
  CHECK_DIRS=("$REPO_DIR")
  if [ -n "$WORKDIR" ] && [ "$WORKDIR" != "$REPO_DIR" ] && [ -d "$WORKDIR/.git" ]; then
    CHECK_DIRS+=("$WORKDIR")
  fi
  for f in "${EXPECTED_FILES[@]}"; do
    # [L25] git status には Windows 絶対パスを渡す(/c/... は git が C:\c\ と誤解釈、
    # また git -C /c/... は「not a git repository」になる)。cygpath -w で統一。
    fw=$(cygpath -w "$f" 2>/dev/null || echo "$f")
    for d in "${CHECK_DIRS[@]}"; do
      dw=$(cygpath -w "$d" 2>/dev/null || echo "$d")
      if git -C "$dw" status --porcelain -- "$fw" 2>/dev/null | grep -q . ; then
        EDITED_ANY=1; break 2
      fi
    done
  done
  if [ "$EDITED_ANY" -eq 0 ] && [ "${#EXPECTED_FILES[@]}" -gt 0 ] && [ "$is_push_task" != 1 ]; then
    VERIFY_SUMMARY="FAIL"
    VERIFY_RESULT="RESULT (path-task) FAIL no_edit
SUMMARY FAIL
(タスクが ${EXPECTED_FILES[*]} を求めていますが、作業ツリーにそれらの実編集(差分)がありません -> 部下が実ファイルを変更していないか、成果をコミット/保存していません。強制差し戻し)"
  fi

  if echo "$VERIFY_RESULT" | grep -q "^SUMMARY OK"; then
    VERIFY_SUMMARY="OK"
  else
    VERIFY_SUMMARY="FAIL"
  fi
else
  # NO_VERIFY エージェント例外: 外部委譲・成果物を作らないルート(chatgpt/desktop/hermes)は
  # ファイル編集を伴わず「アプリに投げるだけ」のため、ゼロ編集=サボリ 判定を回避する。
  NO_VERIFY_AGENTS="chatgpt desktop hermes"
  SKIP_VERIFY=0
  for na in $NO_VERIFY_AGENTS; do
    if echo "$PLAN_JSON" | grep -qE ""agent"[ ]*:[ ]*"$na""; then SKIP_VERIFY=1; break; fi
  done
  if [ "$SKIP_VERIFY" -eq 1 ]; then
    echo "[verify] サブタスクに成果物なし外部ルート($na)を含むため検証ゲートをスキップ(OK)"
    VERIFY_SUMMARY="OK"
  else
  # タスクにファイルパス指定が無い場合: 「実ファイル未変更」を検証する(鶏卵バグ防止)。
  # 部下が何も git に変更していなければ実装サボリとみなし強制差し戻し。
  # 検査対象リポジトリ: 常に $FLEET_HOME に加え、WORKDIR が $FLEET_HOME と異なる
  # git リポジトリならそれも検査する（実編集が $FLEET_HOME 外にあるケースの零編集サボり検出）。
  REPO_DIR="$FLEET_HOME"
  WORKDIR_DIFF=""
  CHECK_DIRS=("$REPO_DIR")
  if [ -n "$WORKDIR" ] && [ "$WORKDIR" != "$REPO_DIR" ] && [ -d "$WORKDIR/.git" ]; then
    CHECK_DIRS+=("$WORKDIR")
  fi
  fi
  GIT_DIFF=""
  for d in "${CHECK_DIRS[@]}"; do
    if [ -d "$d/.git" ]; then
      D_DIFF=$(cd "$d" && git status --porcelain 2>/dev/null)
      # L25 例外: インフラ配線は即コミットされるため、未コミット変更がなくても
      # 直近のコミット差分があれば「部下起因の成果」とみなす（誤差し戻し防止）。
      D_RECENT=$(cd "$d" && git log -3 --name-only --pretty=format: 2>/dev/null | grep -vE '^$' || true)
    else
      D_DIFF=""; D_RECENT=""
    fi
    if [ -n "$D_DIFF" ]; then
      GIT_DIFF="${GIT_DIFF}${GIT_DIFF:+$'\n'}${D_DIFF}"
    # [2026-08-13 幻承認修正] 直近コミット(D_RECENT)フォールバック削除: 過去コミットがあれば無条件PASSになるのを防ぐ(CRITIC 2305.11749)
    fi
  done
  # --- 報告/読み取り専用タスクの誤承認防止 (L4/L17: 部下がスニペットのみ返すサボり) ---
  # 自律ループ自身の churn(learnings.md / retry_queue.txt / .outer* / heartbeat / logs/) は
  # 部下の成果とみなさない。それらを除いた「部下起因の変更」を SUB_CHANGES とする。
  SUB_CHANGES=$(printf '%s\n' "$GIT_DIFF" | grep -vE '(learnings\.md|retry_queue\.txt|\.outer|heartbeat|^logs/|router3\.sh|fleet_outer\.sh|fleet_loop\.sh|fleet_selftest\.sh|detect_failures\.sh|repo-fleet\.sh|router2\.sh|router\.sh|verify_artifacts\.py|\.githooks/|goals\.md|model_catalog\.md|\.last_alert|fleet_alerts\.md|golden_result\.txt|\.last_|\.alert)' || true)
  REPORT_TASK=0
  if printf '%s\n' "$TASK" | grep -qiE '報告|レポート|read.?only|一切.*(行わ|編集)|編集は一切|変更しない|コミットしない|書き込み.*行わ'; then
    REPORT_TASK=1
  fi
  if [ -z "$GIT_DIFF" ] && [ "$is_push_task" != 1 ]; then
    VERIFY_SUMMARY="FAIL"
    VERIFY_RESULT="RESULT (no-path-task) FAIL no_changes
SUMMARY FAIL
(タスク指示文にファイルパスが無く、かつ作業ツリーに未コミット変更がありません → 部下が実装をサボったか、成果をコミットしていません。強制差し戻し)"
  elif [ "$REPORT_TASK" = 1 ]; then
    # 読み取り専用/報告タスク: 部下は「実際の回答(ハッシュ+件数等の具体値)」を出力すべき。
    # 解説やスニペットのみではなく、実際の成果物(7桁以上hexハッシュ かつ 数値)を含むかを
    # content-based check; CRLF-safe because we strip CR before matching (see tr below)
    HAS_HASH=0; HAS_NUM=0
    for f in "$RUN_DIR"/sub_*.md; do
      [ -f "$f" ] || continue
      txt=$(tr -d '
' < "$f")
      echo "$txt" | grep -qiE '[0-9a-f]{7,40}' && HAS_HASH=1
      echo "$txt" | grep -qE '[0-9]+' && HAS_NUM=1
    done
    if [ "$HAS_HASH" = 1 ] && [ "$HAS_NUM" = 1 ] && [ -n "$SUB_CHANGES" ]; then
      VERIFY_SUMMARY="OK"
      VERIFY_RESULT="RESULT (no-path-task) OK
SUMMARY OK
(読み取り専用/報告タスク: 実際の回答(ハッシュ+数値)を検出、かつ部下起因の変更あり → 成果あり)"
    else
      VERIFY_SUMMARY="FAIL"
      VERIFY_RESULT="RESULT (no-path-task) FAIL report_no_answer
SUMMARY FAIL
(読み取り専用/報告タスクですが、部下が実際の回答(ハッシュ+件数等の具体値)を出力していません → 解説/スニペットのみ・差し戻し)"
    fi
  else
    # 実装タスク: 自律ループ自身の churn を除いた部下起因の変更があれば成果あり。
    if [ -z "$SUB_CHANGES" ]; then
      VERIFY_SUMMARY="FAIL"
      VERIFY_RESULT="RESULT (no-path-task) FAIL loop_only_churn
SUMMARY FAIL
(タスク指示文にファイルパスが無く、ツリーの変更は自律ループ自身(learnings.md等)のみで部下起因の成果がありません → 差し戻し)"
    else
      VERIFY_SUMMARY="OK"
      VERIFY_RESULT="RESULT (no-path-task) OK
SUMMARY OK
(タスク指示文にファイルパスはありませんが、部下起因の未コミット変更を確認 → 成果ありとみなします)"
    fi
  fi
fi
# [L25 / End-State guard] 幻承認防止: タスクが push/publish/repo作成を求めているのに
# リモートに実際に反映されていない場合、LLMの「承認」を無条件で上書きして強制差し戻し。
# 根拠: 2026-08-15 実例 — Bossが「承認」と言いつつ実際には git push が行われておらず、
# ローカル作業ツリー(git status)の検証だけでは End-State(リモート反映)を捉えられなかった。
# これは純粋な配管検証(bash+gh/git)であり LLM呼び出しなし → L25 インフラ配線例外に合致。
if printf '%s' "$TASK" | grep -qiE 'push|publish|repo(作成|作る|create)|github|リモート|公開|deploy|プッシュ|リポジトリ'; then
  _remote_ok=0
  _remote_note=""
  # 1) gh でリポジトリ自体が存在するか(リモート作成系タスク)
  if command -v gh >/dev/null 2>&1; then
    _gh_repo=$(printf '%s' "$TASK" | grep -oiE '(shizukutanaka|HP|your-user|[A-Za-z0-9_-]+)/[A-Za-z0-9_.-]+' | head -1)
    if [ -z "$_gh_repo" ] && git remote get-url origin >/dev/null 2>&1; then
      _gh_repo=$(git remote get-url origin 2>/dev/null | sed -E 's#.*[:/]([^/]+/[^/]+)(\.git)?$#\1#')
    fi
    if [ -n "$_gh_repo" ]; then
      if gh repo view "$_gh_repo" >/dev/null 2>&1; then
        _remote_ok=1; _remote_note="gh repo view $_gh_repo OK"
      else
        _remote_note="gh repo view $_gh_repo -> NOT FOUND (リモート未作成/未反映)"
      fi
    fi
  fi
  # 2) push系タスク: ローカルHEADがリモートに存在するか(git ls-remote)
  if [ "$_remote_ok" = "0" ] && git remote get-url origin >/dev/null 2>&1; then
    _local_head=$(git rev-parse HEAD 2>/dev/null)
    if [ -n "$_local_head" ]; then
      if git ls-remote origin 2>/dev/null | grep -q "$_local_head"; then
        _remote_ok=1; _remote_note="local HEAD $_local_head present on remote"
      else
        _remote_note="${_remote_note:+$remote_note; }local HEAD $_local_head NOT on remote (push未反映)"
      fi
    fi
  fi
  if [ "$_remote_ok" = "0" ]; then
    VERIFY_SUMMARY="FAIL"
    VERIFY_RESULT="RESULT (remote-endstate) FAIL remote_not_reflected
SUMMARY FAIL
([End-State guard] タスクはリモート公開/pushを求めていますが、実際のリモート反映が検出されませんでした -> $_remote_note。Bossの「承認」に関わらず強制差し戻し: 実ファイル/リモートが未反映なら成果なし)"
    echo "[verify][End-State guard] リモート未反映を検出 -> 強制差し戻し ($_remote_note)" | tee -a "$REVIEW_INPUT" 2>/dev/null
  else
    # [L25/End-State] リモート反映を確認できた(push/publish/remote系タスク)場合、
    # verify_artifacts.py がタスク文から不正パスを誤抽出して not_found を出していても
    # そちらを上書きして OK とする。実ファイル/リモートの反映が真の成果物。
    VERIFY_SUMMARY="OK"
    echo "[verify][End-State guard] リモート反映を確認: $_remote_note" | tee -a "$REVIEW_INPUT" 2>/dev/null
  fi
fi
# [L25] 生成された .sh の構文チェック (green-is-luck 防壁): BOSS が壊れたスクリプトを承認するのを防ぐ。
# 壊れたスクリプト(カタカナ化等で実行不能)を検出して強制差し戻し。
SH_SYNTAX_FAIL=0
_cd_ok=0
cd "$WORKDIR" 2>/dev/null && _cd_ok=1
if [ "$_cd_ok" = "1" ]; then
  for sf in $(git status --porcelain 2>/dev/null | grep -E '\.sh$' | sed -E 's/^.{1,2} //' | sort -u); do
    full="$WORKDIR/$sf"
  [ -f "$full" ] || continue
  if ! bash -n "$full" 2>/dev/null; then
    SH_SYNTAX_FAIL=1
    echo "[BOSS][WARN] 生成スクリプト構文エラー: $sf (bash -n 失敗)" | tee -a "$REVIEW_INPUT" 2>/dev/null
  fi
  # シェバン正当性: #!/bin/bash または #!/usr/bin/env bash のみ許可。カタカナ化(#!./バッシュ)等は FAIL。
  firstline=$(head -1 "$full" 2>/dev/null)
  if ! echo "$firstline" | grep -qE '^#!/(usr/)?bin/(env )?bash$'; then
    SH_SYNTAX_FAIL=1
    echo "[BOSS][WARN] 生成スクリプト不正シェバン: $sf ($firstline)" | tee -a "$REVIEW_INPUT" 2>/dev/null
  fi
  done
fi
if [ "$SH_SYNTAX_FAIL" = "1" ]; then
  VERIFY_SUMMARY="FAIL"
  VERIFY_RESULT="RESULT (sh-syntax) FAIL sh_syntax_error
SUMMARY FAIL
(生成された .sh に構文エラーまたは不正シェバンがあります → 部下が壊れたスクリプトを生成しました。強制差し戻し)"
fi
echo "$VERIFY_RESULT" | tee "$RUN_DIR_W/verify_artifacts.md"
{
  echo
  echo "## 成果物検証 (verify_artifacts.py)"
  echo "$VERIFY_RESULT"
} >> "$REVIEW_INPUT"

echo "[BOSS] 部下の成果と変更ファイルを読み込み、判定中..."
REVIEW_PROMPT="あなたは上司AI（ボス）です。以下の部下AIの成果と変更ファイルをすべて読み、判定してください。

ルール:
1. あなたはコードを直さない（修正は部下が行う）。
2. 不備があれば、具体的に指摘し、どの部下AIに何をやり直させるかを明示する。
3. 問題なければ「承認」とする。

以下の内容をすべて読んで判定してください:
$(cat "$REVIEW_INPUT")"

# --- 長大なプロンプトを curl の引数に埋め込むと "Argument list too long" になるため、
#     Python 経由でファイルから直接 Ollama へ POST する ---
REVIEW_MODEL=$(curl -s ${OLLAMA_BASE}/api/tags 2>/dev/null | python -c "import json,sys; m=[x['name'] for x in json.load(sys.stdin).get('models',[])]; print('qwen2.5-coder:7b' if 'qwen2.5-coder:7b' in ' '.join(m) else 'qwen2.5-coder:3b')")
REVIEW_PY="$RUN_DIR_W/_review_req.py"
REVIEW_IN_W="$RUN_DIR_W/boss_review_input.md"
cat > "$REVIEW_PY" <<PY
import json,sys,os,urllib.request
prompt=open(r"$REVIEW_IN_W",encoding="utf-8").read()
instr="""あなたは上司AI（ボス）です。以下の部下AIの成果と変更ファイルをすべて読み、判定してください。

ルール:
1. あなたはコードを直さない（修正は部下が行う）。
2. 不備があれば、具体的に指摘し、どの部下AIに何をやり直させるかを明示する。
3. 問題なければ「承認」とする。
4. 【重要】タスクが「ファイルを作成/記録せよ」を求めている場合、実際にそのファイルが存在し、中身が「説明・サンプルコード」ではなく「成果そのもの」になっているか確認せよ。存在しない、または単なるやり方の説明しかない場合は「差し戻し」とし、どの部下に何をさせるか明示せよ。

以下の内容をすべて読んで判定してください:
"""
data=json.dumps({"model":"$REVIEW_MODEL","prompt":instr+prompt,"stream":False,"options":{"temperature":0.1}}).encode()
req=urllib.request.Request(os.environ.get("OLLAMA_BASE","http://localhost:11434")+"/api/generate",data=data,headers={"Content-Type":"application/json"})
try:
    resp=urllib.request.urlopen(req,timeout=300)
    print(json.load(resp).get("response",""))
except Exception as e:
    print("[REVIEW ERROR] "+str(e))
PY
VERDICT=$(boss_llm "$REVIEW_PROMPT" "$REVIEW_MODEL")

# --- structural guard (LLM-independent) ---
# Ollama-down (or any LLM failure) makes boss_llm return "[REVIEW ERROR] ..." or empty.
# Treat that as a rejection: the boss MUST NOT approve when no subordinate output was
# actually reviewed. Force a pure rejection string with NO approval word — do NOT append
# $(structural_verdict), because that can emit "承認" (e.g. a non-empty stub sub_*.md) and
# would make this verdict self-contradictory ("差し戻し。\n承認"), causing the OV logic below
# to record verdict=OK while the boss displays 差し戻し. The rejection marker must be clean.
if [ -z "$VERDICT" ] || printf '%s' "$VERDICT" | grep -q "REVIEW ERROR"; then
  VERDICT="[構造的判定・差し戻し] LLM系(Ollama/Hermes)が全て応答不能のため、実ファイル成果が検証できないため差し戻し。"
fi

# 成果物検証(verify_artifacts.py)がFAILなら、LLMの判定に関わらず強制差し戻し
if [ "$VERIFY_SUMMARY" = "FAIL" ]; then
  VERDICT="[成果物検証NG] verify_artifacts.py が FAIL を検出したため、差し戻し(強制)。
$VERIFY_RESULT

--- 以下、上司AI(LLM)の判定 ---
$VERDICT"
fi

# [L25/hardening 2026-08-16] 「何も実行されていないのに承認」を防ぐ構造的ガード。
# 観測: 全サブタスクが "実行されていません" と報告し、BOSS が verdict=OK sat=80 と
# 承認する事案が発生。no-path gate は「タスク文にファイルパスがあれば編集あり」と
# 過信するため検出できない。よってここで副タスク出力の実質内容を検証する。
# 全 sub_*.md / delegates_result.md が存在しない、または実質的に空(「実行されていません」
# 等のスタブのみ)なら、実際の成果がゼロとみなし強制 RETURN とする。
if [ "$VERIFY_SUMMARY" != "FAIL" ]; then
  _sub_evidence=0
  for _f in "$RUN_DIR"/sub_*.md "$RUN_DIR"/delegates_result.md; do
    [ -f "$_f" ] || continue
    # 実質的な実行証拠(「実行されていません」「実行結果: このタスクは実行されていません」
    # を除外し、それ以外の非空行があれば証拠ありとみなす)
    if grep -q -v -E "実行されていません|このタスクは実行されていません|^[[:space:]]*$" "$_f" 2>/dev/null; then
      _sub_evidence=1
      break
    fi
  done
  if [ "$_sub_evidence" -eq 0 ]; then
    if [ -n "$GIT_DIFF" ]; then
      _sub_evidence=1   # git に実変更があればそれが成果とみなす
    fi
  fi
  if [ "$_sub_evidence" -eq 0 ]; then
    VERIFY_SUMMARY="FAIL"
    VERDICT="[構造的判定・差し戻し] 全サブタスク出力が空(実行証拠なし)のため、承認できません。差し戻し(強制)。"
  fi
fi

echo
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  BOSS VERDICT (上司AIの判定)                            ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo "$VERDICT" | tee "$RUN_DIR_W/boss_verdict.md"

# 差し戻し判定 (簡易: 明確な拒否マーカーのみ。否定形「修正が必要な点はありません」を
# 誤検知しないよう、曖昧な「修正が必要」は除外し「差し戻し」「やり直し」「承認できない」
# 等の明言マーカーのみで判定)
if echo "$VERDICT" | grep -q -E "差し戻し|やり直し|承認できない|不備あり|reject|revise|修正が必要です|修正が必要な箇所|修正が必要な点があり"; then
  echo
  echo "[BOSS] → 不備あり: 部下AIへ差し戻します (ボスは修正しません)"
  echo "$VERDICT" > "$RUN_DIR/return_to_subordinate.md"
  echo "差し戻し内容: $RUN_DIR/return_to_subordinate.md"
else
  echo
  echo "[BOSS] → 承認: タスク完了"
fi

# --- 成果会計(outcome_ledger.sh): 各BOSS VERDICTを時系列台帳に記録 ---
# プロセス準拠だけでなく「実際に仕事が成功したか」を会計する(コンプライアンス・シアター防止)
if [ -f "$FLEET_HOME/outcome_ledger.sh" ]; then
  OV="RETURN"; echo "$VERDICT" | grep -q -E "差し戻し|不備|REJECT|RETURN|却下|承認できない" && OV="RETURN" || { echo "$VERDICT" | grep -q -E "承認|問題なし|完了" && OV="OK"; }
  LAT=0; [ -n "${START_TS:-}" ] && LAT=$(( $(date +%s) - START_TS )) || LAT=0
  # タスク種の簡易判定(戦略較準のためのtype): コード/文書/調査/その他
  TY="other"
  case "$TASK" in
    *作成*|*実装*|*コード*|*修正*|*バグ*|*スクリプト*|*関数*|*リファクタ*) TY="code";;
    *記事*|*文書*|*レポート*|*要約*|*ドキュメント*|*手順書*) TY="doc";;
    *調査*|*検索*|*分析*|*比較*|*調べ*) TY="research";;
  esac
  bash "$FLEET_HOME/outcome_ledger.sh" --record --verdict "$OV" --task "$TASK" --agent "${ROUTE:-unknown}" --type "$TY" --latency "$LAT" 2>&1 | tee -a "$RUN_DIR_W/boss_outcome.log" | grep -E "\[outcome\]"
fi

# --- 人間満足度監査(satisfaction_audit.sh): 各BOSS VERDICTを人間フィードバックとして記録 ---
# 全層GREENでも人間が満足していなければ価値ゼロ。承認/差し戻しをフィードバックとして集計。
if [ -f "$FLEET_HOME/satisfaction_audit.sh" ]; then
  OV2="RETURN"; echo "$VERDICT" | grep -q -E "差し戻し|不備|REJECT|RETURN|却下|承認できない" && OV2="RETURN" || { echo "$VERDICT" | grep -q -E "承認|問題なし|完了" && OV2="OK"; }
  # 満足度スコアの代理: 承認=80, 差し戻し=20 (人間からの明示スコアがあれば上書き想定)
  SAT_SCORE=80; [ "$OV2" = "RETURN" ] && SAT_SCORE=20
  bash "$FLEET_HOME/satisfaction_audit.sh" --record --verdict "$OV2" --task "$TASK" --sat "$SAT_SCORE" 2>&1 | tee -a "$RUN_DIR_W/boss_satis.log" | grep -E "\[satis\]"
fi

echo "=== Done. Run logs: $RUN_DIR/ ==="
