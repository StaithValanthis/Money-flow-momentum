#!/bin/bash
# Status for Demo instance. Uses config/config.demo.yaml.
# Usage: ./scripts/status_demo.sh

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
if [ -f venv/bin/activate ]; then source venv/bin/activate; fi
exec python run_bot.py status --config config/config.demo.yaml
