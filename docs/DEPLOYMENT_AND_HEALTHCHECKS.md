# Deployment and Health Checks

## Startup Checks

Before running the bot, ensure:

- **Environment**: Required env vars (e.g. API keys) are set if running live/paper.
- **Config**: `config/config.yaml` exists or defaults are acceptable.
- **Directories**: DB directory (e.g. `data/`) and artifact dirs are writable. Use `ensure_artifact_dirs()` or create `artifacts/`, `artifacts/evaluations`, etc.
- **Testnet vs mainnet**: Config and `.env` should match intended environment.

## CLI Commands (Ubuntu / Linux)

From repo root with venv activated:

### Burn-in (validation)
```bash
python3 run_bot.py burnin status
python3 run_bot.py burnin report --window 24
python3 run_bot.py burnin readiness
python3 run_bot.py burnin readiness --output artifacts/burnin
```
Use when `burn_in.burn_in_enabled` is true to see gate breaches and readiness. See **docs/BURN_IN_AND_LIVE_VALIDATION.md**.

### Health check
```bash
python3 run_bot.py health
# Optional: --heartbeat path/to/heartbeat.json  --stale-sec 300
```
Exits 0 if OK; 1 if heartbeat missing, heartbeat file stale (default >5min), or any loop reported stale/fail. Per-loop last_ok age is shown.

### Status / diagnostics
```bash
python3 run_bot.py status
# Optional: --heartbeat path
```
Prints active config, database path, stage5_enabled, active_strategy, artifact dir existence, and (when heartbeat file exists) heartbeat age and per-loop freshness.

### Report (summary)
```bash
python3 run_bot.py report
# Optional: --heartbeat path
```
Prints active config, degradation events (24h), recent promotions, and loop health/stale summary from heartbeat. If no heartbeat file, reports that runtime loop state is unknown.

## Heartbeat and Health

The running bot **writes the heartbeat file** from its main loops: context refresh, public/private WS, reconciliation, lifecycle, score/entry loop, and degradation monitor each call `report_ok(...)`; the score/entry loop writes `artifacts/heartbeat.json` about every 30 seconds. Commands `health`, `status`, and `report` read this file to show real loop freshness and detect stale or missing loops.

## Systemd

- Use the provided systemd service file; set `User` and `WorkingDirectory` appropriately.
- `journalctl -u money-flow-momentum -f` for logs.
- Ensure `data/` and `artifacts/` are writable by the service user.

## Caveats

- If the bot is not running, there is no heartbeat file; `health` will exit 1 with "No heartbeat file". `status` and `report` handle missing heartbeat gracefully.
- No built-in startup check for exchange connectivity; the bot will fail on first request if keys or network are wrong.
