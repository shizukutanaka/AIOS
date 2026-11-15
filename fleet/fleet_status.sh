#!/bin/bash
FLEET_HOME="${FLEET_HOME:-$(cd "$(dirname "$0")" && pwd)}"  # fleet/ layer root (was ~/agent-fleet in the original repo; override with $FLEET_HOME)

# フリートループの死活(PID/PPID)
echo "Fleet Loop Status:"
ps -ef | grep '[f]leet_loop' | awk '{print "PID: "$2", PPID: "$3}'

# モデル基盤(Ollama互換API)の死活 — psではなくHTTPで判定(Windowsトレイはpsに出ない)
echo -e "\nModel Backend (Ollama-compatible API):"
if curl -s -m 8 http://localhost:11434/api/version >/dev/null 2>&1; then
    echo "backend alive (http://localhost:11434)"
else
    echo "backend dead"
fi

# VRAM 使用率 — nvidia-smi が使えない環境ではスキップ
echo -e "\nVRAM Usage:"
if command -v nvidia-smi >/dev/null 2>&1; then
    used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')
    if [ -n "$used" ]; then echo "VRAM used: $used"; else echo "VRAM: n/a (driver)"; fi
else
    echo "VRAM: n/a (no nvidia-smi)"
fi

# Watchdog cron — Hermes が内部管理(ジョブID f6c75a7c89b4 等)。crontab/ファイルではなくスキップ
echo -e "\nWatchdog Cron Job:"
echo "watchdog: hermes-managed (skip file check)"

# 直近の配信ファイル(README.md/OPSLOG.md)のタイムスタンプ
echo -e "\nLatest Delivery File Timestamps:"
if [ -f "$FLEET_HOME/README.md" ]; then
    echo "README.md: $(stat -c %y "$FLEET_HOME/README.md")"
fi
if [ -f "$FLEET_HOME/OPSLOG.md" ]; then
    echo "OPSLOG.md: $(stat -c %y "$FLEET_HOME/OPSLOG.md")"
fi

# 未コミット変更の有無
echo -e "\nUncommitted Changes:"
_status=""
cd "$FLEET_HOME" 2>/dev/null && _status=$(git status --porcelain 2>/dev/null)
if [ -n "$_status" ]; then
    echo "There are uncommitted changes."
else
    echo "No uncommitted changes."
fi
