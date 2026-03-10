#!/bin/bash
# Validate environment and config. Run from repo root.
# Usage: ./scripts/validate_env.sh

set -e
cd "$(dirname "$0")/.."
if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi
echo "=== Environment validation ==="
python run_bot.py validate
echo "Done."
