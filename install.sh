#!/bin/bash
# Money Flow Momentum - Ubuntu installer

set -e

echo "=== Money Flow Momentum Installer ==="

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Python 3 not found. Install: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python $PYVER"

# Create venv
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "Created venv"
fi

source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install deps
pip install -r requirements.txt

# Create dirs
mkdir -p data config logs
echo "Created data/, config/, logs/"

# Copy config if not exists
if [ ! -f config/config.yaml ]; then
    cp config/config.yaml.example config/config.yaml 2>/dev/null || true
    echo "Copy config/config.yaml.example to config/config.yaml and edit"
fi

# Optional: systemd
read -p "Install systemd service? (y/n): " INSTALL_SD
if [ "$INSTALL_SD" = "y" ] || [ "$INSTALL_SD" = "Y" ]; then
    SVC_PATH="/etc/systemd/system/money-flow-momentum.service"
    sudo tee "$SVC_PATH" << EOF
[Unit]
Description=Money Flow Momentum Trading Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python run_bot.py run
Restart=on-failure
RestartSec=10
Environment=PATH=$(pwd)/venv/bin

[Install]
WantedBy=multi-user.target
EOF
    echo "Installed $SVC_PATH"
    echo "Enable: sudo systemctl enable money-flow-momentum"
    echo "Start:  sudo systemctl start money-flow-momentum"
fi

echo ""
echo "Install complete. Run: source venv/bin/activate && python run_bot.py run"
