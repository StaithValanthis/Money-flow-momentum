#!/bin/bash
# Backup config to artifacts/validation or CONFIG_BACKUP_DIR. Optional timestamp subdir.
# Usage: ./scripts/backup_config.sh [--timestamp]
# --timestamp: create artifacts/validation/backups/YYYYMMDD_HHMMSS/ and copy config there.

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
BACKUP_ROOT="${CONFIG_BACKUP_DIR:-$REPO_ROOT/artifacts/validation}"
TIMESTAMP=0
[ "${1:-}" = "--timestamp" ] && TIMESTAMP=1

if [ ! -f "config/config.yaml" ]; then
    echo "No config/config.yaml to back up."
    exit 0
fi

if [ "$TIMESTAMP" = "1" ]; then
    TS=$(date -u +%Y%m%d_%H%M%S 2>/dev/null || date +%Y%m%d_%H%M%S)
    DEST="$BACKUP_ROOT/backups/$TS"
else
    DEST="$BACKUP_ROOT"
fi
mkdir -p "$DEST"
cp config/config.yaml "$DEST/config.yaml"
echo "Backed up config to $DEST/config.yaml"
