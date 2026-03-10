#!/bin/bash
# Safe promote environment: Demo -> guarded Live.
# Runs Python promote-env (preview by default). Use --confirm-live to apply; optionally --start-live.
# Usage: ./scripts/promote_demo_to_live.sh [--confirm-live] [--start-live] [--reason "reason"]
# Example preview:  ./scripts/promote_demo_to_live.sh
# Example apply:    ./scripts/promote_demo_to_live.sh --confirm-live --reason "demo burn-in passed"
# Example apply+start: ./scripts/promote_demo_to_live.sh --confirm-live --start-live

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi
exec python run_bot.py promote-env "$@"
