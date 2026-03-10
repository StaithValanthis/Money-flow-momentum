#!/bin/bash
# Money Flow Momentum - Ubuntu installer
# Run from repo root. Usage: ./install.sh

set -e

echo "=== Money Flow Momentum Installer ==="

# Python version check (3.11+)
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found. Install: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi
PYVER=$(python3 -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>/dev/null || true)
if [ -z "$PYVER" ]; then
    echo "ERROR: Could not detect Python version."
    exit 1
fi
PYMIN=$(python3 -c "import sys; v=sys.version_info; print(v.major * 100 + v.minor)" 2>/dev/null || true)
if [ -n "$PYMIN" ] && [ "$PYMIN" -lt 311 ]; then
    echo "WARN: Python $PYVER detected. 3.11+ recommended. Continuing anyway."
fi
echo "Python $PYVER"

# Create venv if missing
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "Created venv"
fi

# Activate venv (best-effort on Windows)
if [ -f "venv/bin/activate" ]; then
    set +u
    source venv/bin/activate
    set -u
fi

# Upgrade pip and install requirements
pip install --upgrade pip
pip install -r requirements.txt

# Writable directories
mkdir -p data/db logs artifacts artifacts/burnin artifacts/validation
if [ -n "${CONFIG_BACKUP_DIR:-}" ]; then
    mkdir -p "$CONFIG_BACKUP_DIR"
fi
echo "Created data/db, logs, artifacts, artifacts/burnin, artifacts/validation"

# Config: ensure example exists
if [ ! -f "config/config.yaml.example" ]; then
    echo "ERROR: config/config.yaml.example not found. Cannot copy."
    exit 1
fi

# Optional copy of example to config.yaml if missing
if [ ! -f "config/config.yaml" ]; then
    cp config/config.yaml.example config/config.yaml
    echo "Copied config/config.yaml.example -> config/config.yaml (edit before run)"
else
    echo "Config config/config.yaml already exists"
fi

# .env
if [ ! -f ".env" ]; then
    echo ""
    echo "WARN: .env not found. For paper/live you need API keys."
    echo "  Run: python bootstrap_config.py"
    echo "  Or create .env from .env.example (BYBIT_ENV=demo, BYBIT_DEMO_API_KEY/SECRET, BYBIT_LIVE_API_KEY/SECRET)."
fi

echo "--- Next steps ---"
echo "1. Run bootstrap: python3 bootstrap_config.py (with BYBIT_ENV=demo, config will have burn-in enabled for demo)."
echo "2. Validate: source venv/bin/activate && python3 run_bot.py validate"
echo "3. Show runtime mode: python3 run_bot.py show-runtime-mode"
echo "4. Start demo burn-in: ./scripts/start_testnet_burnin.sh (see docs/INSTALL_AND_RUN_GUIDE.md)"
echo "5. Optional systemd: ./scripts/install_systemd.sh"
echo "Install complete."
