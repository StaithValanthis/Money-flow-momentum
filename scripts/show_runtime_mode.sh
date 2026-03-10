#!/bin/bash
# Show current runtime mode, burn-in phase, active config, strategy; warn on mismatches.
# Usage: ./scripts/show_runtime_mode.sh

cd "$(dirname "$0")/.."
if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi
python run_bot.py show-runtime-mode
