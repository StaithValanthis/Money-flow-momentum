#!/bin/bash
# Run automation status and show latest recommendation. Usage: ./scripts/automation_status.sh
# Requires venv and repo root. Does not start any service.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d "venv" ] || [ ! -x "venv/bin/python" ]; then
    echo "venv not found; run from repo root with venv created."
    exit 1
fi

echo "=== Automation status (python run_bot.py automation status) ==="
venv/bin/python run_bot.py automation status

if [ -f "artifacts/automation/automation_status.md" ]; then
    echo ""
    echo "=== Latest recommendation (artifacts/automation/automation_status.md) ==="
    cat artifacts/automation/automation_status.md
fi
