#!/bin/bash
# Run bot in paper mode
cd "$(dirname "$0")/.."
source venv/bin/activate 2>/dev/null || true
export BYBIT_TESTNET=true
python run_bot.py run
