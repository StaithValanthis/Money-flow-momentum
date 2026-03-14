#!/bin/bash
# Start Demo research instance (operating_mode=demo_research). Uses config/config.demo.yaml and .env.demo.
# Paths: data/demo/, artifacts/demo/, logs/demo/. Safe to run alongside Live instance.
# Usage: ./scripts/start_demo_research.sh [--foreground]

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
CONFIG="${REPO_ROOT}/config/config.demo.yaml"
FOREGROUND=0
[ "${1:-}" = "--foreground" ] && FOREGROUND=1

if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi

echo "=== Demo research instance (config.demo.yaml) ==="
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: $CONFIG not found. Copy config/config.yaml.example to config/config.demo.yaml and set operating_mode: demo_research"
    exit 1
fi
python run_bot.py validate --config "$CONFIG" || exit 1
# Demo initialization: resume checkpoint if present, search until passable config, activate seed. Do not start trading if init fails.
echo "=== Demo initialization ==="
if ! python run_bot.py demo init --config "$CONFIG"; then
    echo "Demo initialization did not find a passable config. Demo trading will not start."
    exit 1
fi
if [ "$FOREGROUND" = "1" ]; then
    exec python run_bot.py run --config "$CONFIG"
fi
if systemctl is-active --quiet money-flow-momentum-demo.service 2>/dev/null; then
    sudo systemctl restart money-flow-momentum-demo.service
    echo "Restarted money-flow-momentum-demo.service"
else
    echo "Run in foreground with: $0 --foreground"
    echo "Or install systemd: ./scripts/install_systemd.sh --dual-instance"
    exec python run_bot.py run --config "$CONFIG"
fi
