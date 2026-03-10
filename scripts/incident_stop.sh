#!/bin/bash
# Incident / safe stop: stop service, show recent logs, latest burn-in report/readiness, optional rollback.
# Usage: ./scripts/incident_stop.sh [--rollback "reason"]

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
ROLLBACK_REASON=""
while [ $# -gt 0 ]; do
    if [ "$1" = "--rollback" ]; then
        ROLLBACK_REASON="${2:-incident rollback}"
        shift 2
        break
    fi
    shift
done

echo "=== Incident stop ==="

echo "1. Stopping service..."
sudo systemctl stop money-flow-momentum 2>/dev/null || true
echo "Service stopped (or was not running)."

echo ""
echo "2. Recent logs (last 80 lines)..."
if [ -f "$REPO_ROOT/logs/bot.log" ]; then
    tail -n 80 "$REPO_ROOT/logs/bot.log"
else
    echo "No logs/bot.log found."
fi

echo ""
echo "3. Latest burn-in readiness (if any)..."
READINESS=$(ls -t artifacts/burnin/readiness_*.md 2>/dev/null | head -1)
if [ -n "$READINESS" ]; then
    echo "--- $READINESS ---"
    cat "$READINESS"
else
    echo "No readiness artifact found."
fi

echo ""
echo "4. Burn-in report..."
if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
    python run_bot.py burnin report 2>/dev/null || true
fi

if [ -n "$ROLLBACK_REASON" ]; then
    echo ""
    echo "5. Rollback (reason: $ROLLBACK_REASON)..."
    python run_bot.py config rollback --reason "$ROLLBACK_REASON" 2>/dev/null || true
fi

echo ""
echo "--- Next steps ---"
echo "  Logs: $REPO_ROOT/logs/bot.log"
echo "  Readiness: artifacts/burnin/readiness_*.md"
echo "  Burn-in report: python run_bot.py burnin report"
echo "  To rollback config: ./scripts/incident_stop.sh --rollback \"reason\""
echo "  Then: ./scripts/start_testnet_burnin.sh or ./scripts/start_small_live.sh"
