#!/bin/bash
# Quick health check
cd "$(dirname "$0")/.."
echo "=== Bot Health Check ==="
echo "Python: $(python3 --version 2>/dev/null || echo 'not found')"
echo "Venv: $([ -d venv ] && echo 'OK' || echo 'missing')"
echo "Config: $([ -f config/config.yaml ] && echo 'OK' || echo 'missing')"
echo ".env: $([ -f .env ] && echo 'OK' || echo 'missing')"
echo "DB: $([ -f data/bot.db ] && echo 'exists' || echo 'not yet created')"
