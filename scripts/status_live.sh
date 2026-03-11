#!/bin/bash
# Status for Live instance. Uses config/config.live.yaml.
# Usage: ./scripts/status_live.sh

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
if [ -f venv/bin/activate ]; then source venv/bin/activate; fi
exec python run_bot.py status --config config/config.live.yaml
