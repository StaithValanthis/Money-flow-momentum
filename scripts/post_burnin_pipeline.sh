#!/bin/bash
# Post-burn-in pipeline helper: readiness -> evaluation -> optimizer -> candidates -> optional shadow.
# This is a manual-first helper. It does NOT auto-promote or switch Demo -> Live.
# Usage:
#   ./scripts/post_burnin_pipeline.sh [--from-date YYYY-MM-DD] [--to-date YYYY-MM-DD] [--config-id <id>] [--n-samples N] [--window H]
#   ./scripts/post_burnin_pipeline.sh --start-shadow
#   ./scripts/post_burnin_pipeline.sh --start-shadow --shadow-report

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

ARGS=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --from-date|--to-date|--config-id|--n-samples|--window|--output)
            ARGS+=("$1" "$2"); shift 2;;
        --start-shadow|--shadow-report)
            ARGS+=("$1"); shift 1;;
        *)
            ARGS+=("$1"); shift 1;;
    esac
done

if [ -f venv/bin/activate ]; then
    # shellcheck disable=SC1091
    . venv/bin/activate
fi

python run_bot.py post-burnin "${ARGS[@]}"

