#!/bin/bash
# Start demo burn-in (recommended): validate, confirm phase=demo and burn_in_enabled, optional backup, start bot.
# Legacy: phase=testnet with BYBIT_ENV=testnet also supported; prefer demo for Bybit Demo Trading.
# Usage: ./scripts/start_testnet_burnin.sh [--no-backup] [--foreground]
# --foreground: run bot in foreground instead of starting systemd service.

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
BACKUP=1
FOREGROUND=0
for a in "$@"; do
    [ "$a" = "--no-backup" ] && BACKUP=0
    [ "$a" = "--foreground" ] && FOREGROUND=1
done

echo "=== Demo burn-in start (BYBIT_ENV=demo) ==="

if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi

echo "1. Validating environment..."
if ! python run_bot.py validate; then
    echo "ERROR: Validation failed. Fix errors above and run: python run_bot.py validate"
    exit 1
fi

echo "2. Checking burn-in config (burn_in_enabled=true, burn_in_phase=demo) and DEMO environment..."
python run_bot.py show-runtime-mode 2>/dev/null || true
if ! python -c "
from pathlib import Path
from src.config.config import load_config, resolve_bybit_credentials, get_bybit_env
c, env = load_config(Path('config/config.yaml'))
b = getattr(c, 'burn_in', None)
if not b or not getattr(b, 'burn_in_enabled', False):
    print('ERROR: burn_in.burn_in_enabled is not true. Set burn_in.burn_in_enabled: true in config.')
    exit(1)
phase = getattr(b, 'burn_in_phase', '')
if phase not in ('demo', 'testnet'):
    print('ERROR: burn_in.burn_in_phase must be demo (recommended) or testnet for this script. Current:', phase)
    exit(1)
env_type = get_bybit_env(env)
if phase == 'demo' and env_type != 'demo':
    print('ERROR: For demo burn-in set BYBIT_ENV=demo in .env. Current BYBIT_ENV:', env_type or 'not set')
    exit(1)
if phase == 'testnet' and env_type != 'testnet':
    print('ERROR: For testnet burn-in set BYBIT_ENV=testnet in .env. Current:', env_type)
    exit(1)
key, secret, legacy, _ = resolve_bybit_credentials(env, env_type)
if not key or not secret:
    print('ERROR: Credentials missing. For demo set BYBIT_DEMO_API_KEY/SECRET; for testnet set BYBIT_TESTNET_API_KEY/SECRET (or legacy keys) in .env')
    exit(1)
if legacy:
    print('WARN: Using legacy single-key mode. Recommend dual-key: BYBIT_DEMO_API_KEY/SECRET and BYBIT_LIVE_API_KEY/SECRET')
print('OK: burn_in_enabled=true, burn_in_phase=%s, environment=%s, keys present' % (phase, env_type.upper()))
" 2>/dev/null; then
    echo "Fix config and .env then re-run."
    exit 1
fi

if [ "$BACKUP" = "1" ]; then
    echo "3. Backing up config..."
    ./scripts/backup_config.sh 2>/dev/null || true
fi

echo "4. Starting bot..."
if [ "$FOREGROUND" = "1" ]; then
    echo "Running in foreground. Ctrl+C to stop."
    python run_bot.py run
else
    if systemctl is-active --quiet money-flow-momentum 2>/dev/null; then
        echo "Service already running. Restarting."
        sudo systemctl restart money-flow-momentum
    else
        sudo systemctl start money-flow-momentum
    fi
    echo "Service started."
    echo ""
    echo "--- Monitor commands ---"
    echo "  ./scripts/check_burnin.sh"
    echo "  ./scripts/tail_logs.sh"
    echo "  python run_bot.py health"
    echo "  python run_bot.py burnin readiness --output artifacts/burnin"
fi
