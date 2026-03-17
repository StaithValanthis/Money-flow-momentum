#!/bin/bash
# Start Demo research instance (operating_mode=demo_research). Uses config/config.demo.yaml and .env.demo.
# Paths: data/demo/, artifacts/demo/, logs/demo/. Safe to run alongside Live instance.
# When demo_probation.auto_reinit_after_failure is true, probation failure triggers re-init and restart loop.
# When warm_start.retry_init_until_passable is true, init is retried after no passable config (exit 21).
# Usage: ./scripts/start_demo_research.sh [--foreground]
#
# Exit codes from demo init:
#   0  = success (passable config found)
#   21 = no passable config found, retryable (script sleeps and retries init)
#   1 or other = hard failure; script exits
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
# Exit code from demo init when no passable config found (retry after delay)
EXIT_NO_PASSABLE_CONFIG_RETRY=21

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

# Retry config for init (used when init returns 21)
RETRY_SLEEP=300
MAX_RETRIES=0
if tmp=$(python run_bot.py demo init-retry-config --config "$CONFIG" 2>/dev/null); then
    RETRY_SLEEP=$(echo "$tmp" | head -1)
    MAX_RETRIES=$(echo "$tmp" | tail -1)
fi

# Check if auto_reinit_after_failure is enabled so we can run the recovery loop
AUTO_REINIT=0
if python run_bot.py demo probation auto-reinit-enabled --config "$CONFIG" 2>/dev/null; then
    AUTO_REINIT=1
fi

run_demo_init() {
    echo "=== Demo initialization ==="
    python run_bot.py demo init --config "$CONFIG"
    return $?
}

run_demo_runtime() {
    python run_bot.py run --config "$CONFIG"
}

# Run init until success (0) or hard failure (not 21). On 21: log, sleep, retry (respecting MAX_RETRIES).
# Must not let set -e trigger on init exit 21; we need to capture and handle it.
run_init_until_success() {
    attempt=1
    while true; do
        export DEMO_INIT_ATTEMPT=$attempt
        set +e
        run_demo_init
        INIT_EC=$?
        set -e
        if [ "$INIT_EC" -eq 0 ]; then
            echo "Demo initialization succeeded; starting Demo runtime."
            return 0
        fi
        if [ "$INIT_EC" -eq "$EXIT_NO_PASSABLE_CONFIG_RETRY" ]; then
            echo "No passable config found; retrying initialization in $RETRY_SLEEP seconds"
            if [ "$MAX_RETRIES" -gt 0 ] && [ "$attempt" -ge "$MAX_RETRIES" ]; then
                echo "Max init retry attempts ($MAX_RETRIES) reached; stopping."
                exit 1
            fi
            sleep "$RETRY_SLEEP"
            attempt=$((attempt + 1))
            continue
        fi
        echo "Demo init failed (exit $INIT_EC); stopping."
        exit "$INIT_EC"
    done
}

if [ "$AUTO_REINIT" = "1" ]; then
    # Full loop: init (with retries on 21) -> runtime; on runtime exit 20 re-init
    # Must not let set -e trigger on runtime exit 20; we need to capture and handle it.
    while true; do
        run_init_until_success
        set +e
        run_demo_runtime
        RUNTIME_EC=$?
        set -e
        if [ "$RUNTIME_EC" -eq 0 ]; then
            echo "Demo runtime exited normally; not restarting."
            exit 0
        fi
        if [ "$RUNTIME_EC" -eq "$EXIT_PROBATION_REINIT" ]; then
            echo "Demo runtime exited due to probation failure; re-initializing."
            continue
        fi
        exit "$RUNTIME_EC"
    done
else
    # Single init (with retries on 21) + run once
    run_init_until_success
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
