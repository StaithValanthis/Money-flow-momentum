#!/bin/bash
# Start Demo research instance (operating_mode=demo_research). Uses config/config.demo.yaml and .env.demo.
# Paths: data/demo/, artifacts/demo/, logs/demo/. Safe to run alongside Live instance.
# When demo_probation.auto_reinit_after_failure is true, probation failure triggers re-init and restart loop.
# Usage: ./scripts/start_demo_research.sh [--foreground]
#
# Exit codes from Demo runtime:
#   0  = normal clean exit (operator stop, etc.)
#   20 = probation failure with auto_reinit_after_failure (script re-runs demo init and restarts)
#   other = error; script exits with that code

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
CONFIG="${REPO_ROOT}/config/config.demo.yaml"
FOREGROUND=0
[ "${1:-}" = "--foreground" ] && FOREGROUND=1

# Exit code from bot when probation failed and re-init is requested (Demo-only)
EXIT_PROBATION_REINIT=20

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

# Check if auto_reinit_after_failure is enabled so we can run the recovery loop
AUTO_REINIT=0
if python run_bot.py demo probation auto-reinit-enabled --config "$CONFIG" 2>/dev/null; then
    AUTO_REINIT=1
fi

run_demo_init() {
    echo "=== Demo initialization ==="
    if ! python run_bot.py demo init --config "$CONFIG"; then
        echo "No passable config found; Demo trading will remain stopped."
        return 1
    fi
    echo "Demo initialization succeeded; starting Demo runtime."
    return 0
}

run_demo_runtime() {
    # When in auto-reinit loop we always run in this process to capture exit code (e.g. 20).
    python run_bot.py run --config "$CONFIG"
}

if [ "$AUTO_REINIT" = "1" ]; then
    # Recovery loop: on probation failure (exit 20) re-run demo init and start again.
    # Runtime runs in this process so we can capture exit code.
    while true; do
        run_demo_init || exit 1
        run_demo_runtime
        EC=$?
        if [ $EC -eq 0 ]; then
            echo "Demo runtime exited normally; not restarting."
            exit 0
        fi
        if [ $EC -eq $EXIT_PROBATION_REINIT ]; then
            echo "Demo runtime exited due to probation failure; re-initializing."
            continue
        fi
        exit $EC
    done
else
    # Single init + run (no loop)
    run_demo_init || exit 1
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
fi
