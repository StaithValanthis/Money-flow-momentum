#!/bin/bash
# Guarded small-live start: validate burn_in_enabled=true, burn_in_phase=live_small, mainnet keys, stricter limits; start service.
# Usage: ./scripts/start_small_live.sh [--foreground]

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
FOREGROUND=0
[ "${1:-}" = "--foreground" ] && FOREGROUND=1

echo "=== Guarded small-live start ==="

if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi

echo "1. Validating environment..."
python run_bot.py validate || exit 1

echo "2. Checking burn_in_enabled=true, burn_in_phase=live_small, LIVE environment and live keys..."
python -c "
from pathlib import Path
from src.config.config import load_config, resolve_bybit_credentials, get_bybit_env
c, env = load_config(Path('config/config.yaml'))
b = getattr(c, 'burn_in', None)
if not b or not getattr(b, 'burn_in_enabled', False):
    print('ERROR: burn_in.burn_in_enabled must be true for small-live.')
    exit(1)
phase = getattr(b, 'burn_in_phase', '')
if phase != 'live_small':
    print('ERROR: burn_in.burn_in_phase must be live_small. Current:', phase)
    exit(1)
env_type = get_bybit_env(env)
if env_type != 'live':
    print('ERROR: BYBIT_ENV must be live for guarded live. Set BYBIT_ENV=live in .env')
    exit(1)
key, secret, legacy, _ = resolve_bybit_credentials(env, 'live')
if not key or not secret:
    print('ERROR: Live credentials missing. Set BYBIT_LIVE_API_KEY/SECRET (or legacy BYBIT_API_KEY/SECRET) in .env')
    exit(1)
if c.exchange.testnet:
    print('WARN: exchange.testnet is true (overridden by BYBIT_ENV=live).')
if legacy:
    print('WARN: Using legacy single-key mode. Recommend dual-key: BYBIT_DEMO_API_KEY/SECRET and BYBIT_LIVE_API_KEY/SECRET')
print('OK: burn_in enabled, phase=live_small, environment=LIVE, live keys present')
" || exit 1

echo "3. Starting service..."
if [ "$FOREGROUND" = "1" ]; then
    python run_bot.py run
else
    sudo systemctl start money-flow-momentum 2>/dev/null || sudo systemctl restart money-flow-momentum
    echo "Service started."
    echo ""
    echo "--- Post-start verification ---"
    echo "  ./scripts/check_burnin.sh"
    echo "  ./scripts/tail_logs.sh 100"
    echo "  python run_bot.py health"
fi
