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

echo "2. Checking burn_in_enabled=true, burn_in_phase=live_small..."
python -c "
from pathlib import Path
from src.config.config import load_config
c, env = load_config(Path('config/config.yaml'))
b = getattr(c, 'burn_in', None)
if not b or not getattr(b, 'burn_in_enabled', False):
    print('ERROR: burn_in.burn_in_enabled must be true for small-live.')
    exit(1)
phase = getattr(b, 'burn_in_phase', '')
if phase != 'live_small':
    print('ERROR: burn_in.burn_in_phase must be live_small. Current:', phase)
    exit(1)
# Mainnet: exchange.testnet should be false for real live
if c.exchange.testnet:
    print('WARN: exchange.testnet is true. For real mainnet set exchange.testnet: false')
if not env.bybit_api_key or not env.bybit_api_secret:
    print('ERROR: BYBIT_API_KEY and BYBIT_API_SECRET required for live.')
    exit(1)
print('OK: burn_in enabled, phase=live_small, API keys present')
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
