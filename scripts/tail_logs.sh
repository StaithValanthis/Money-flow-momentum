#!/bin/bash
# Tail bot logs. Usage: ./scripts/tail_logs.sh [lines]
# Default: 50 lines. Log file: logs/bot.log (or from repo root).

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${REPO_ROOT}/logs/bot.log"
LINES="${1:-50}"
if [ ! -f "$LOG" ]; then
    echo "Log file not found: $LOG"
    exit 1
fi
tail -n "$LINES" -f "$LOG"
