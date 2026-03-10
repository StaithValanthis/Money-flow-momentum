#!/bin/bash
# Generate burn-in bundle: status, report, burnin status/report/readiness; write to artifacts/burnin and artifacts/validation.
# Usage: ./scripts/generate_burnin_bundle.sh [output_dir]
# output_dir defaults to artifacts/burnin (readiness/status also under artifacts/validation if needed).

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
OUT_DIR="${1:-artifacts/burnin}"
mkdir -p "$OUT_DIR"
mkdir -p artifacts/validation

if [ -f venv/bin/activate ]; then
    set +u
    source venv/bin/activate
    set -u
fi

TS=$(date -u +%Y%m%d_%H%M%S 2>/dev/null || date +%Y%m%d_%H%M%S)
BUNDLE_DIR="$OUT_DIR/bundle_$TS"
mkdir -p "$BUNDLE_DIR"

echo "=== Generating burn-in bundle at $BUNDLE_DIR ==="

echo "Running status..." && python run_bot.py status > "$BUNDLE_DIR/status.txt" 2>&1 || true
echo "Running report..." && python run_bot.py report > "$BUNDLE_DIR/report.txt" 2>&1 || true
echo "Running burnin status..." && python run_bot.py burnin status > "$BUNDLE_DIR/burnin_status.txt" 2>&1 || true
echo "Running burnin report..." && python run_bot.py burnin report > "$BUNDLE_DIR/burnin_report.txt" 2>&1 || true
echo "Running burnin readiness..." && python run_bot.py burnin readiness --output "$BUNDLE_DIR" 2>&1 | tee "$BUNDLE_DIR/readiness_console.txt" || true

# Summary markdown
SUMMARY="$BUNDLE_DIR/summary.md"
{
    echo "# Burn-in bundle — $TS"
    echo ""
    echo "## Status"
    echo '```'
    cat "$BUNDLE_DIR/status.txt" 2>/dev/null || echo "N/A"
    echo '```'
    echo ""
    echo "## Burn-in readiness"
    echo '```'
    cat "$BUNDLE_DIR/readiness_console.txt" 2>/dev/null || echo "N/A"
    echo '```'
    echo ""
    echo "## Files"
    ls -la "$BUNDLE_DIR" 2>/dev/null || true
} > "$SUMMARY"

echo "Bundle written to $BUNDLE_DIR"
echo "Summary: $SUMMARY"
