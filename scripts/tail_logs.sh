#!/bin/bash
# Tail bot or automation logs.
# Usage:
#   ./scripts/tail_logs.sh [lines]           # single-instance: logs/bot.log
#   ./scripts/tail_logs.sh demo [lines]      # Demo instance: logs/demo/bot.log
#   ./scripts/tail_logs.sh live [lines]      # Live instance: logs/live/bot.log
#   ./scripts/tail_logs.sh automation [lines]  # legacy automation service
#   ./scripts/tail_logs.sh demo automation [lines]  # Demo automation journal

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${REPO_ROOT}/logs/bot.log"
AUTOMATION_SVC="money-flow-momentum-automation.service"
DEMO_AUTOMATION_SVC="money-flow-momentum-demo-automation.service"

if [ "${1:-}" = "demo" ] || [ "${1:-}" = "live" ]; then
    INSTANCE="$1"
    shift
    LOG="${REPO_ROOT}/logs/${INSTANCE}/bot.log"
    if [ "${1:-}" = "automation" ]; then
        shift
        LINES="${1:-50}"
        if command -v journalctl >/dev/null 2>&1; then
            sudo journalctl -u "$DEMO_AUTOMATION_SVC" -n "$LINES" -f
        else
            echo "journalctl not available"; exit 1
        fi
        exit 0
    fi
    LINES="${1:-50}"
    if [ ! -f "$LOG" ]; then
        echo "Log file not found: $LOG"; exit 1
    fi
    tail -n "$LINES" -f "$LOG"
    exit 0
fi

if [ "${1:-}" = "automation" ]; then
    shift
    LINES="${1:-50}"
    if command -v journalctl >/dev/null 2>&1; then
        sudo journalctl -u "$AUTOMATION_SVC" -n "$LINES" -f
    else
        echo "journalctl not available"; exit 1
    fi
else
    LINES="${1:-50}"
    if [ ! -f "$LOG" ]; then
        echo "Log file not found: $LOG"; exit 1
    fi
    tail -n "$LINES" -f "$LOG"
fi
