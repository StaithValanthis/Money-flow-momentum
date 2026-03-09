#!/bin/bash
# Run bot in live mode - USE WITH CAUTION
cd "$(dirname "$0")/.."
source venv/bin/activate 2>/dev/null || true
export BYBIT_TESTNET=false
# Ensure config mode is live
python run_bot.py run
