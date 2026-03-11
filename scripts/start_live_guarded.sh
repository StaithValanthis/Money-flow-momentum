#!/bin/bash
# Start Live guarded instance (operating_mode=live_guarded). Uses config/config.live.yaml and .env.live.
# Paths: data/live/, artifacts/live/, logs/live/. Safe to run alongside Demo instance.
# Usage: ./scripts/start_live_guarded.sh [--foreground]

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
CONFIG="${REPO_ROOT}/config/config.live.yaml"
FOREGROUND=0
[ "${1:-}" = "--foreground" ] && FOREGROUND=1

if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi

echo "=== Live guarded instance (config.live.yaml) ==="
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: $CONFIG not found. Create config/config.live.yaml with operating_mode: live_guarded"
    exit 1
fi
python run_bot.py validate --config "$CONFIG" || exit 1
./scripts/check_small_live_ready.sh 2>/dev/null || true
if [ "$FOREGROUND" = "1" ]; then
    exec python run_bot.py run --config "$CONFIG"
fi
if systemctl is-active --quiet money-flow-momentum-live.service 2>/dev/null; then
    sudo systemctl restart money-flow-momentum-live.service
    echo "Restarted money-flow-momentum-live.service"
else
    echo "Run in foreground with: $0 --foreground"
    echo "Or install systemd: ./scripts/install_systemd.sh --dual-instance"
    exec python run_bot.py run --config "$CONFIG"
fi
