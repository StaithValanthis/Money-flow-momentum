# Deployment and Health Checks

## Startup Checks

Before running the bot, ensure:

- **Environment**: Required env vars (e.g. API keys) are set if running live/paper.
- **Config**: `config/config.yaml` exists or defaults are acceptable.
- **Directories**: DB directory (e.g. `data/`) and artifact dirs are writable. Use `ensure_artifact_dirs()` or create `artifacts/`, `artifacts/evaluations`, etc.
- **Testnet vs mainnet**: Config and `.env` should match intended environment.

**Validation command (recommended before first run and after config changes):**

```bash
python run_bot.py validate
# or: ./scripts/validate_env.sh
```

**Canonical install/run:** See **docs/INSTALL_AND_RUN_GUIDE.md** for the full Ubuntu install and run workflow.

Exits 0 if config, credentials for **selected environment** (demo/live/testnet via BYBIT_ENV), dirs, mode/env consistency, and active strategy are OK. With dual-key, validation ensures the selected environment has a key pair (demo or live); mismatches (e.g. BYBIT_ENV=demo but only live keys set) fail clearly. When validation passes, the CLI prints readiness hints for demo burn-in vs guarded small-live. See **docs/BURN_IN_OPERATOR_RUNBOOK.md** for the full install → validate → burn-in → live → evaluate → optimize → shadow → promote/rollback workflow and script reference.

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

### Promote environment (Demo -> Live)
```bash
python3 run_bot.py promote-env                    # preview only
python3 run_bot.py promote-env --confirm-live    # apply switch (backs up .env and config)
python3 run_bot.py promote-env --confirm-live --reason "demo burn-in passed"
# or: ./scripts/promote_demo_to_live.sh [--confirm-live] [--start-live]
```
Does **not** auto-promote; requires `--confirm-live` to change `.env` and config. Checks readiness (READY_FOR_SMALL_LIVE) and live credentials first. Writes `artifacts/validation/env_promotion_<ts>.json` and `.md`. See **docs/BURN_IN_OPERATOR_RUNBOOK.md** (section 8a).

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

- Use `./scripts/install_systemd.sh` to install the main bot unit and (optionally) the automation timer. Use `--no-automation` to skip the timer. Substitutes repo path and user in service files; set `User` and `WorkingDirectory` if needed.
- **Two units:** (1) **Main bot** (`money-flow-momentum.service`) — trading. (2) **Automation timer** (`money-flow-momentum-automation.timer`) — runs `python run_bot.py automation cycle` every 15 min; does not start the bot and does not auto-promote config or environment. Enable/start the timer only when Demo orchestration is desired; the main bot should be running separately.
- **Status:** `./scripts/service_status.sh` (both), `./scripts/service_status.sh bot`, `./scripts/service_status.sh automation`, or `sudo systemctl status money-flow-momentum` / `sudo systemctl status money-flow-momentum-automation.timer`
- **Logs:** `./scripts/tail_logs.sh` (main bot) or `./scripts/tail_logs.sh automation` (automation cycle); or `journalctl -u money-flow-momentum -f` / `journalctl -u money-flow-momentum-automation.service -f`
- **Automation status:** `./scripts/automation_status.sh`
- Ensure `data/` and `artifacts/` are writable by the service user.

## Caveats

- If the bot is not running, there is no heartbeat file; `health` will exit 1 with "No heartbeat file". `status` and `report` handle missing heartbeat gracefully.
- No built-in startup check for exchange connectivity; the bot will fail on first request if keys or network are wrong.
