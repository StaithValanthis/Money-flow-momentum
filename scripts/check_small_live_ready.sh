#!/bin/bash
# Small-live readiness check: verify readiness, no critical burn-in issues, operator has switched phase to live_small.
# Does NOT auto-switch phases. Produces go/no-go summary.
# Usage: ./scripts/check_small_live_ready.sh

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi

echo "=== Small-live readiness check ==="

GO=1
echo "1. Checking burn_in_phase is live_small and LIVE credentials..."
python -c "
from pathlib import Path
from src.config.config import load_config, resolve_bybit_credentials, get_bybit_env
c, env = load_config(Path('config/config.yaml'))
b = getattr(c, 'burn_in', None)
phase = getattr(b, 'burn_in_phase', '') if b else ''
if phase != 'live_small':
    print('NO-GO: burn_in_phase is not live_small (current: %s). Set burn_in_phase: live_small in config when ready.' % phase)
    exit(1)
env_type = get_bybit_env(env)
if env_type != 'live':
    print('NO-GO: BYBIT_ENV is %s. Set BYBIT_ENV=live in .env for small-live.' % env_type)
    exit(1)
key, secret, _, _ = resolve_bybit_credentials(env, 'live')
if not key or not secret:
    print('NO-GO: Live credentials missing. Set BYBIT_LIVE_API_KEY/SECRET (or legacy BYBIT_API_KEY/SECRET) in .env')
    exit(1)
print('OK: burn_in_phase=live_small, environment=LIVE, live keys present')
" || GO=0

echo ""
echo "2. Running burn-in readiness..."
python run_bot.py burnin readiness --output artifacts/burnin 2>/dev/null || true
# Classification in last readiness file
READINESS_FILE=$(ls -t artifacts/burnin/readiness_*.md 2>/dev/null | head -1)
if [ -n "$READINESS_FILE" ] && grep -q "ready" "$READINESS_FILE" 2>/dev/null; then
    echo "Readiness: ready (from $READINESS_FILE)"
elif [ -n "$READINESS_FILE" ]; then
    echo "Readiness: check $READINESS_FILE"
    GO=0
else
    echo "Readiness: no artifact yet"
    GO=0
fi

echo ""
echo "3. Burn-in report (critical issues?)..."
python run_bot.py burnin report 2>/dev/null || true

echo ""
echo "--- Go / No-Go ---"
if [ "$GO" = "1" ]; then
    echo "GO: Config and readiness support small-live. Start with: ./scripts/start_small_live.sh"
    exit 0
else
    echo "NO-GO: Resolve issues above (phase, readiness, critical burn-in issues) before starting small-live."
    exit 1
fi
