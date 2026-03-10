#!/bin/bash
# Tail bot or automation logs. Usage: ./scripts/tail_logs.sh [lines] | ./scripts/tail_logs.sh automation [lines]
# Default: 50 lines. Bot log file: logs/bot.log. Automation: journalctl for money-flow-momentum-automation.service.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${REPO_ROOT}/logs/bot.log"
AUTOMATION_SVC="money-flow-momentum-automation.service"

if [ "${1:-}" = "automation" ]; then
    shift
    LINES="${1:-50}"
    if command -v journalctl >/dev/null 2>&1; then
        sudo journalctl -u "$AUTOMATION_SVC" -n "$LINES" -f
    else
        echo "journalctl not available"
        exit 1
    fi
else
    LINES="${1:-50}"
    if [ ! -f "$LOG" ]; then
        echo "Log file not found: $LOG"
        exit 1
    fi
    tail -n "$LINES" -f "$LOG"
fi
