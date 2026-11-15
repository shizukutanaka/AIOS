#!/usr/bin/env bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)
# goal_complete.sh — 目標の完了オラクル抽出/実行 (L25: インフラ配線・純 bash + python 解析)
# 使い方:
#   goal_complete.sh --extract "<goal text>"   done_when コマンドを stdout へ(無ければ空・exit 0)
#   goal_complete.sh --check  "<goal text>"   done_when を実行: 0=完了 / 1=未完了 / 2=オラクル無し
# 注: <goal text> は goals.md 内の該当行の部分文字列、または done_when: を含む全文でも可。
set -u
AGENT_DIR="${AGENT_DIR:-$FLEET_HOME}"
GOALS="$AGENT_DIR/goals.md"
MODE="${1:-}"
GOAL="${2:-}"
[ -z "$GOAL" ] && { echo "usage: $0 --extract|--check '<goal>'"; exit 2; }
python - "$MODE" "$GOAL" "$GOALS" <<'PY'
import sys, re, subprocess, os
mode, goal, goals = sys.argv[1], sys.argv[2], sys.argv[3]

def run(cmd):
    if mode == '--extract':
        print(cmd); return 0
    r = subprocess.run(cmd, shell=True)
    return 0 if r.returncode == 0 else 1

# Case A: goal text itself contains done_when: -> parse directly
dm = re.search(r'done_when:\s*(.+?)\s*$', goal)
if dm:
    sys.exit(run(dm.group(1).strip()))

# Case B: search goals.md for a line containing the goal substring
if os.path.isfile(goals):
    s = open(goals, encoding='utf-8').read()
    for line in s.split('\n'):
        m = re.match(r'^\s*-\s*\[\s\]\s*(.*)$', line)
        if not m:
            continue
        body = m.group(1)
        if goal.strip() not in body:
            continue
        dm = re.search(r'done_when:\s*(.+?)\s*$', body)
        if dm:
            sys.exit(run(dm.group(1).strip()))
        sys.exit(2)  # matched but no oracle
sys.exit(2)  # not found / no oracle
PY
