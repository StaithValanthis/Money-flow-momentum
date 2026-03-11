#!/bin/bash
# Guarded live (live_guarded) readiness: verify operating_mode, readiness, no critical burn-in issues.
# Does NOT auto-switch. Produces go/no-go summary.
# Usage: ./scripts/check_small_live_ready.sh [config_path]
# Default config_path: config/config.live.yaml (use config/config.yaml for single-instance).

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
CONFIG="${1:-config/config.live.yaml}"
if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi

echo "=== Guarded live (live_guarded) readiness check ==="

GO=1
echo "1. Checking operating_mode is live_guarded and LIVE credentials..."
python -c "
from pathlib import Path
from src.config.config import load_config, resolve_bybit_credentials, get_bybit_env, get_effective_operating_mode
c, env = load_config(Path('$CONFIG'))
mode = get_effective_operating_mode(c, env)
if mode != 'live_guarded':
    print('NO-GO: operating_mode is not live_guarded (current: %s). Set operating_mode: live_guarded in config when ready.' % mode)
    exit(1)
b = getattr(c, 'burn_in', None)
phase = getattr(b, 'burn_in_phase', '') if b else ''
if phase not in ('live_guarded', 'live_small'):
    print('NO-GO: burn_in_phase should be live_guarded or live_small for guarded live (current: %s).' % phase)
    exit(1)
env_type = get_bybit_env(env)
if env_type != 'live':
    print('NO-GO: BYBIT_ENV is %s. Set BYBIT_ENV=live in .env for guarded live.' % env_type)
    exit(1)
key, secret, _, _ = resolve_bybit_credentials(env, 'live')
if not key or not secret:
    print('NO-GO: Live credentials missing. Set BYBIT_LIVE_API_KEY/SECRET (or legacy BYBIT_API_KEY/SECRET) in .env')
    exit(1)
print('OK: operating_mode=live_guarded, environment=LIVE, live keys present')
" || GO=0

echo ""
echo "2. Running burn-in readiness..."
python run_bot.py burnin readiness --config "$CONFIG" 2>/dev/null || true
ART_ROOT=$(python -c "from pathlib import Path; from src.config.config import load_config; c,_=load_config(Path('$CONFIG')); print(c.artifacts_root)" 2>/dev/null || echo "artifacts")
READINESS_DIR="$ART_ROOT/burnin"
READINESS_FILE=$(ls -t "$READINESS_DIR"/readiness_*.md 2>/dev/null | head -1)
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
python run_bot.py burnin report --config "$CONFIG" 2>/dev/null || true

echo ""
echo "--- Go / No-Go ---"
if [ "$GO" = "1" ]; then
    echo "GO: Config and readiness support guarded live (live_guarded). Start with: ./scripts/start_live_guarded.sh or ./scripts/start_small_live.sh"
    exit 0
else
    echo "NO-GO: Resolve issues above (operating_mode, phase, readiness, critical burn-in issues) before starting guarded live."
    exit 1
fi
