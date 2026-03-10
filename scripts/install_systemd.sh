#!/bin/bash
# Install systemd unit for Money Flow Momentum. Run from repo root.
# Usage: ./scripts/install_systemd.sh [--user]

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

REPO_USER="${SUDO_USER:-$USER}"
REPO_GROUP="$(id -gn "$REPO_USER" 2>/dev/null || echo "$REPO_USER")"
UNIT_NAME="money-flow-momentum.service"

if [ "${1:-}" = "--user" ]; then
    UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$UNIT_DIR"
    SVC_PATH="$UNIT_DIR/$UNIT_NAME"
    sed -e "s|REPO_ROOT|$REPO_ROOT|g" -e "s|REPO_USER|$USER|g" -e "s|REPO_GROUP|$(id -gn)|g" \
        "$REPO_ROOT/money-flow-momentum.service" > "$SVC_PATH"
    systemctl --user daemon-reload
    echo "Installed $SVC_PATH (user)"
    echo "Enable: systemctl --user enable $UNIT_NAME"
    echo "Start:  systemctl --user start $UNIT_NAME"
else
    SVC_PATH="/etc/systemd/system/$UNIT_NAME"
    sed -e "s|REPO_ROOT|$REPO_ROOT|g" -e "s|REPO_USER|$REPO_USER|g" -e "s|REPO_GROUP|$REPO_GROUP|g" \
        "$REPO_ROOT/money-flow-momentum.service" | sudo tee "$SVC_PATH" > /dev/null
    sudo systemctl daemon-reload
    echo "Installed $SVC_PATH"
    echo "Enable: sudo systemctl enable $UNIT_NAME"
    echo "Start:  sudo systemctl start $UNIT_NAME"
fi
