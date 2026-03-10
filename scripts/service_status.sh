#!/bin/bash
# Show service status. Usage: ./scripts/service_status.sh [--user]

UNIT_NAME="money-flow-momentum.service"
if [ "${1:-}" = "--user" ]; then
    systemctl --user status "$UNIT_NAME"
else
    sudo systemctl status "$UNIT_NAME"
fi
