#!/bin/bash
# Show service status (main bot and automation timer). Usage: ./scripts/service_status.sh [--user] [bot|automation]

UNIT_NAME="money-flow-momentum.service"
AUTOMATION_SVC="money-flow-momentum-automation.service"
AUTOMATION_TIMER="money-flow-momentum-automation.timer"

which="${2:-all}"
if [ "${1:-}" = "--user" ]; then
    USER_OPT="--user"
    shift
    which="${1:-all}"
else
    USER_OPT=""
    which="${1:-all}"
fi

_status() {
    if [ "$USER_OPT" = "--user" ]; then
        systemctl --user status "$1"
    else
        sudo systemctl status "$1"
    fi
}

if [ "$which" = "bot" ] || [ "$which" = "main" ]; then
    _status "$UNIT_NAME"
elif [ "$which" = "automation" ] || [ "$which" = "timer" ]; then
    echo "=== Automation timer ==="
    if [ "$USER_OPT" = "--user" ]; then
        systemctl --user status "$AUTOMATION_TIMER"
    else
        sudo systemctl status "$AUTOMATION_TIMER"
    fi
    echo ""
    echo "=== Last automation run (service) ==="
    if [ "$USER_OPT" = "--user" ]; then
        systemctl --user status "$AUTOMATION_SVC"
    else
        sudo systemctl status "$AUTOMATION_SVC"
    fi
else
    echo "=== Main bot service ==="
    _status "$UNIT_NAME"
    echo ""
    echo "=== Automation timer ==="
    if [ "$USER_OPT" = "--user" ]; then
        systemctl --user status "$AUTOMATION_TIMER"
    else
        sudo systemctl status "$AUTOMATION_TIMER"
    fi
    echo ""
    echo "=== Last automation cycle run ==="
    if [ "$USER_OPT" = "--user" ]; then
        systemctl --user status "$AUTOMATION_SVC"
    else
        sudo systemctl status "$AUTOMATION_SVC"
    fi
fi
