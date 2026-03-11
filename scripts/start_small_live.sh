#!/bin/bash
# Guarded live start (live_guarded mode): validate operating_mode=live_guarded, burn_in phase, mainnet keys; start service.
# Uses config/config.live.yaml when present (dual-instance), else config/config.yaml.
# Usage: ./scripts/start_small_live.sh [--foreground]

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
FOREGROUND=0
[ "${1:-}" = "--foreground" ] && FOREGROUND=1
CONFIG="config/config.yaml"
[ -f "$REPO_ROOT/config/config.live.yaml" ] && CONFIG="config/config.live.yaml"

echo "=== Guarded live (live_guarded) start ==="

if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi

echo "1. Validating environment..."
python run_bot.py validate --config "$CONFIG" || exit 1

echo "2. Checking operating_mode=live_guarded, burn_in enabled, LIVE environment and live keys..."
python -c "
from pathlib import Path
from src.config.config import load_config, resolve_bybit_credentials, get_bybit_env, get_effective_operating_mode
c, env = load_config(Path(\"$CONFIG\"))
mode = get_effective_operating_mode(c, env)
if mode != 'live_guarded':
    print('ERROR: operating_mode must be live_guarded for guarded live. Current:', mode, '- Set operating_mode: live_guarded in config.')
    exit(1)
b = getattr(c, 'burn_in', None)
if not b or not getattr(b, 'burn_in_enabled', False):
    print('ERROR: burn_in.burn_in_enabled must be true for guarded live.')
    exit(1)
phase = getattr(b, 'burn_in_phase', '')
if phase not in ('live_guarded', 'live_small'):
    print('ERROR: burn_in.burn_in_phase must be live_guarded or live_small for guarded live. Current:', phase)
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
print('OK: operating_mode=live_guarded, burn_in enabled, phase=%s, environment=LIVE, live keys present' % phase)
" || exit 1

echo "3. Starting service..."
if [ "$FOREGROUND" = "1" ]; then
    python run_bot.py run --config "$CONFIG"
else
    if systemctl is-active --quiet money-flow-momentum-live.service 2>/dev/null; then
        sudo systemctl restart money-flow-momentum-live.service
        echo "Restarted money-flow-momentum-live.service"
    elif systemctl is-active --quiet money-flow-momentum.service 2>/dev/null; then
        sudo systemctl restart money-flow-momentum.service
        echo "Restarted money-flow-momentum.service"
    else
        echo "Run in foreground: $0 --foreground"
        echo "Or install systemd: ./scripts/install_systemd.sh (single) or ./scripts/install_systemd.sh --dual-instance"
        python run_bot.py run --config "$CONFIG"
    fi
fi

