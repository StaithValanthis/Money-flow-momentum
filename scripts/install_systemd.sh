#!/bin/bash
# Install systemd units for Money Flow Momentum.
# Usage:
#   ./scripts/install_systemd.sh              # single-instance (legacy): main + automation
#   ./scripts/install_systemd.sh --dual-instance   # demo + live instances + demo automation
#   ./scripts/install_systemd.sh [--user] [--no-automation]

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

REPO_USER="${SUDO_USER:-$USER}"
REPO_GROUP="$(id -gn "$REPO_USER" 2>/dev/null || echo "$REPO_USER")"
UNIT_NAME="money-flow-momentum.service"
AUTOMATION_SVC="money-flow-momentum-automation.service"
AUTOMATION_TIMER="money-flow-momentum-automation.timer"
DEMO_SVC="money-flow-momentum-demo.service"
LIVE_SVC="money-flow-momentum-live.service"
DEMO_AUTOMATION_SVC="money-flow-momentum-demo-automation.service"
DEMO_AUTOMATION_TIMER="money-flow-momentum-demo-automation.timer"

INSTALL_AUTOMATION=true
DUAL_INSTANCE=false
for arg in "$@"; do
    if [ "$arg" = "--no-automation" ]; then
        INSTALL_AUTOMATION=false
    elif [ "$arg" = "--dual-instance" ]; then
        DUAL_INSTANCE=true
    fi
done

_install_one() {
    local src="$1"
    local dest="$2"
    if [ -f "$src" ]; then
        sed -e "s|REPO_ROOT|$REPO_ROOT|g" -e "s|REPO_USER|$REPO_USER|g" -e "s|REPO_GROUP|$REPO_GROUP|g" "$src" > "$dest"
        echo "Installed $dest"
    fi
}

_install_dual() {
    if [ "${1:-}" = "--user" ]; then
        UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
        mkdir -p "$UNIT_DIR"
        _install_one "$REPO_ROOT/$DEMO_SVC" "$UNIT_DIR/$DEMO_SVC"
        _install_one "$REPO_ROOT/$LIVE_SVC" "$UNIT_DIR/$LIVE_SVC"
        _install_one "$REPO_ROOT/$DEMO_AUTOMATION_SVC" "$UNIT_DIR/$DEMO_AUTOMATION_SVC"
        cp "$REPO_ROOT/$DEMO_AUTOMATION_TIMER" "$UNIT_DIR/$DEMO_AUTOMATION_TIMER"
        echo "Installed $UNIT_DIR/$DEMO_AUTOMATION_TIMER"
        systemctl --user daemon-reload
        echo ""
        echo "Demo instance:  systemctl --user enable $DEMO_SVC  ; systemctl --user start $DEMO_SVC"
        echo "Live instance:  systemctl --user enable $LIVE_SVC  ; systemctl --user start $LIVE_SVC"
        echo "Demo automation: systemctl --user enable $DEMO_AUTOMATION_TIMER ; systemctl --user start $DEMO_AUTOMATION_TIMER"
    else
        _install_one "$REPO_ROOT/$DEMO_SVC" "/tmp/$DEMO_SVC"
        sudo mv "/tmp/$DEMO_SVC" "/etc/systemd/system/$DEMO_SVC"
        _install_one "$REPO_ROOT/$LIVE_SVC" "/tmp/$LIVE_SVC"
        sudo mv "/tmp/$LIVE_SVC" "/etc/systemd/system/$LIVE_SVC"
        _install_one "$REPO_ROOT/$DEMO_AUTOMATION_SVC" "/tmp/$DEMO_AUTOMATION_SVC"
        sudo mv "/tmp/$DEMO_AUTOMATION_SVC" "/etc/systemd/system/$DEMO_AUTOMATION_SVC"
        sudo cp "$REPO_ROOT/$DEMO_AUTOMATION_TIMER" "/etc/systemd/system/$DEMO_AUTOMATION_TIMER"
        sudo systemctl daemon-reload
        echo ""
        echo "Demo instance:  sudo systemctl enable $DEMO_SVC  ; sudo systemctl start $DEMO_SVC"
        echo "Live instance:  sudo systemctl enable $LIVE_SVC  ; sudo systemctl start $LIVE_SVC"
        echo "Demo automation: sudo systemctl enable $DEMO_AUTOMATION_TIMER ; sudo systemctl start $DEMO_AUTOMATION_TIMER"
    fi
}

if [ "$DUAL_INSTANCE" = true ]; then
    _install_dual "$@"
    exit 0
fi

# Single-instance (legacy) path
if [ "${1:-}" = "--user" ]; then
    UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$UNIT_DIR"
    _install_one "$REPO_ROOT/money-flow-momentum.service" "$UNIT_DIR/$UNIT_NAME"
    if [ "$INSTALL_AUTOMATION" = true ]; then
        _install_one "$REPO_ROOT/$AUTOMATION_SVC" "$UNIT_DIR/$AUTOMATION_SVC"
        cp "$REPO_ROOT/$AUTOMATION_TIMER" "$UNIT_DIR/$AUTOMATION_TIMER"
        echo "Installed $UNIT_DIR/$AUTOMATION_TIMER"
    fi
    systemctl --user daemon-reload
    echo ""
    echo "Enable main bot: systemctl --user enable $UNIT_NAME"
    echo "Start main bot:  systemctl --user start $UNIT_NAME"
    if [ "$INSTALL_AUTOMATION" = true ]; then
        echo "Enable automation timer: systemctl --user enable $AUTOMATION_TIMER"
        echo "Start automation timer:  systemctl --user start $AUTOMATION_TIMER"
    fi
else
    _install_one "$REPO_ROOT/money-flow-momentum.service" "/tmp/$UNIT_NAME"
    sudo mv "/tmp/$UNIT_NAME" "/etc/systemd/system/$UNIT_NAME"
    if [ "$INSTALL_AUTOMATION" = true ]; then
        _install_one "$REPO_ROOT/$AUTOMATION_SVC" "/tmp/$AUTOMATION_SVC"
        sudo mv "/tmp/$AUTOMATION_SVC" "/etc/systemd/system/$AUTOMATION_SVC"
        sudo cp "$REPO_ROOT/$AUTOMATION_TIMER" "/etc/systemd/system/$AUTOMATION_TIMER"
    fi
    sudo systemctl daemon-reload
    echo ""
    echo "Enable main bot: sudo systemctl enable $UNIT_NAME"
    echo "Start main bot:  sudo systemctl start $UNIT_NAME"
    if [ "$INSTALL_AUTOMATION" = true ]; then
        echo "Enable automation timer: sudo systemctl enable $AUTOMATION_TIMER"
        echo "Start automation timer:  sudo systemctl start $AUTOMATION_TIMER"
    fi
fi
