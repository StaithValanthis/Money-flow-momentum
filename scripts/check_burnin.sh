#!/bin/bash
# Burn-in check: health, status, burnin status/report/readiness; summarize healthy / needs review / blocked.
# Usage: ./scripts/check_burnin.sh

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi

echo "=== Burn-in check ==="

SUMMARY="needs_review"
echo "--- Health ---"
if python run_bot.py health 2>/dev/null; then
    echo "Health: OK"
    SUMMARY="healthy"
else
    echo "Health: FAIL or bot not running"
    SUMMARY="blocked"
fi

echo ""
echo "--- Status ---"
python run_bot.py status 2>/dev/null || true

echo ""
echo "--- Burn-in status ---"
python run_bot.py burnin status 2>/dev/null || true

echo ""
echo "--- Burn-in report ---"
python run_bot.py burnin report 2>/dev/null || true

echo ""
echo "--- Burn-in readiness ---"
python run_bot.py burnin readiness --output artifacts/burnin 2>/dev/null || true

echo ""
echo "--- Summary ---"
if [ "$SUMMARY" = "blocked" ]; then
    echo "Result: BLOCKED (health failed or bot not running)"
    echo "Artifacts: artifacts/burnin/ artifacts/validation/"
    exit 2
fi
if [ "$SUMMARY" = "healthy" ]; then
    echo "Result: HEALTHY — continue monitoring. Run ./scripts/check_burnin.sh periodically."
else
    echo "Result: NEEDS_REVIEW — review outputs above before scaling."
fi
echo "Artifacts: artifacts/burnin/ artifacts/validation/"
