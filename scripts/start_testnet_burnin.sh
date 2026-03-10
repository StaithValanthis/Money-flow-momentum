#!/bin/bash
# Start testnet burn-in: validate, confirm phase=testnet and burn_in_enabled, optional backup, start bot.
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

echo "=== Testnet burn-in start ==="

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

echo "2. Checking burn-in config (burn_in_enabled=true, burn_in_phase=testnet)..."
python run_bot.py show-runtime-mode 2>/dev/null || true
if ! python -c "
from pathlib import Path
from src.config.config import load_config
c, _ = load_config(Path('config/config.yaml'))
b = getattr(c, 'burn_in', None)
if not b or not getattr(b, 'burn_in_enabled', False):
    print('ERROR: burn_in.burn_in_enabled is not true. Set burn_in.burn_in_enabled: true in config.')
    exit(1)
phase = getattr(b, 'burn_in_phase', '')
if phase != 'testnet':
    print('ERROR: burn_in.burn_in_phase must be testnet for this script. Current:', phase)
    exit(1)
print('OK: burn_in_enabled=true, burn_in_phase=testnet')
" 2>/dev/null; then
    echo "Fix config and re-run."
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
