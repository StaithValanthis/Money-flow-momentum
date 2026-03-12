# Money Flow Momentum

Production-ready Bybit V5 cross-sectional flow impulse trading bot for linear USDT perpetuals.

## Operating modes

The bot has **two top-level operating modes** (set in `config/config.yaml` as `operating_mode:`):

- **`demo_research`** – Autonomous Demo: trade, collect data, evaluate, optimize, and auto-start shadow on Bybit Demo. No manual approval during research. Only genuine safety issues (kill switch, protection mismatch, execution drift, stale health) block. Use for research and tuning.
- **`live_guarded`** – Guarded Live: stricter limits and readiness expectations. Config promotion and Demo → Live environment promotion **require explicit manual confirmation**. Use for production live deployment.

If `operating_mode` is omitted, it is derived from environment and existing automation/burn-in settings (Demo + automation + demo phase → `demo_research`; otherwise `live_guarded`). See [docs/INSTALL_AND_RUN_GUIDE.md](docs/INSTALL_AND_RUN_GUIDE.md) and [docs/DEPLOYMENT_AND_HEALTHCHECKS.md](docs/DEPLOYMENT_AND_HEALTHCHECKS.md).

## Dual-instance operation (recommended)

You can run **two isolated instances** on the same host at once:

- **Demo research instance** — `operating_mode: demo_research`, `BYBIT_ENV=demo`. Uses `config/config.demo.yaml`, `.env.demo`, `data/demo/bot.db`, `artifacts/demo/`, `logs/demo/`. Only this instance runs the automation timer (evaluate, optimize, shadow, recommendation). Config promotion and Demo → Live remain **manual**.
- **Live guarded instance** — `operating_mode: live_guarded`, `BYBIT_ENV=live`. Uses `config/config.live.yaml`, `.env.live`, `data/live/bot.db`, `artifacts/live/`, `logs/live/`. No automation timer; operator manually promotes configs and environment when appropriate.

The instances do **not** share DB, artifacts, logs, or heartbeat. Use instance-specific config and env so each process knows its identity. See [docs/INSTALL_AND_RUN_GUIDE.md](docs/INSTALL_AND_RUN_GUIDE.md) for exact commands (validate, start, status, tail, promote) per instance.

## Features

- **Real-time flow metrics**: Aggressive buy/sell volume, delta, CVD, VWAP, buy/sell ratio from public trade stream
- **Cross-sectional ranking**: Scores all symbols, trades top long/short candidates
- **Context refresh**: Scheduled klines, OI, funding, long/short ratio; staleness gating for entries
- **WS sharding**: Multiple public WS connections when universe exceeds 50 symbols
- **Private reconciliation**: Orders, fills, positions via private WS + REST fallback; orphan recovery
- **Lifecycle management**: TP1/TP2, breakeven move, time stop, flow-reversal exit
- **Robust risk management**: ATR stops, daily drawdown/loss kill switch, max positions per side, circuit breaker
- **Bybit V5**: REST + WebSocket; **demo** (api-demo.bybit.com, stream-demo.bybit.com for private; mainnet public data) and mainnet; optional testnet (legacy)
- **Deployable**: Ubuntu CLI, systemd service, log rotation
- **Stage 3 (adaptive framework)**: Config versioning, evaluation reports, walk-forward optimization, guardrails, shadow comparison, promotion/rollback, degradation monitoring (see [docs/STAGE3_ADAPTIVE_FRAMEWORK.md](docs/STAGE3_ADAPTIVE_FRAMEWORK.md))
- **Stage 4 (strategy refinement)**: Flow feature expansion, regime filters, adaptive thresholds, improved ranking, exit refinements (exhaustion/failed-breakout), cluster controls, Stage 4 evaluation metrics and optimizer params (see [docs/STAGE4_STRATEGY_REFINEMENT.md](docs/STAGE4_STRATEGY_REFINEMENT.md), [docs/REGIME_FILTERS_AND_THRESHOLDS.md](docs/REGIME_FILTERS_AND_THRESHOLDS.md))
- **Stage 5 (platform & portfolio)**: Portfolio risk budgeting, **candidate-set allocator** (allocates across multiple candidates, prefers stronger scores when budgets are tight), exposure controls, strategy registry, replay/fill model, **heartbeat written by runtime loops** (context refresh, WS, reconciliation, lifecycle, score/entry, degradation monitor), health/status/report CLI with real loop freshness (see [docs/STAGE5_PLATFORM_AND_PORTFOLIO.md](docs/STAGE5_PLATFORM_AND_PORTFOLIO.md), [docs/MONITORING_AND_ALERTING.md](docs/MONITORING_AND_ALERTING.md), [docs/DEPLOYMENT_AND_HEALTHCHECKS.md](docs/DEPLOYMENT_AND_HEALTHCHECKS.md))
- **Burn-in / live validation**: Optional **burn-in mode** with stricter limits, execution audit, protection-state audit, gate breaches, and readiness reporting for **Bybit Demo** (recommended) and small-cap live rollout (see [docs/BURN_IN_AND_LIVE_VALIDATION.md](docs/BURN_IN_AND_LIVE_VALIDATION.md))
- **Fixed-equity Demo research (optional)**: When `operating_mode: demo_research` and `demo_research.fixed_equity_enabled: true`, the Demo instance can size positions using a **fixed synthetic research equity** (e.g. $1000) instead of the actual wallet balance, so research results are not distorted by large Demo balances. Demo can also use **relaxed kill-switch** thresholds and **permissive burn-in** limits for long-running optimizer tuning. Live is unchanged and always uses actual equity and strict limits. See `demo_research` in config and [docs/INSTALL_AND_RUN_GUIDE.md](docs/INSTALL_AND_RUN_GUIDE.md).
- **Demo automation (optional)**: Config-driven Demo orchestration that, when enabled, automatically runs readiness → evaluation → optimizer → candidate generation → shadow → recommendation for Demo burn-in, while keeping **config promotion** and **Demo → Live promotion** strictly manual (see `python run_bot.py automation --help`). When **Demo auto-adopt** is enabled (`automation.auto_adopt_demo_candidates: true`), a better candidate that meets the adoption rules can be automatically activated as the Demo instance’s active config; the new config is used **after the next Demo restart**. Live is never auto-updated; promoting a Demo candidate to Live still requires explicit operator action (`promote-to-live`, `promote-env`).

- **Warm-start calibration (Demo-only)**: On a **fresh Demo install** or when local Demo trade count is below a threshold, the system can run a **warm-start** before first trading: it loads historical candle data (from the exchange or local cache), runs a calibration/optimization pass, selects a viable seed config, and activates it for the Demo instance. This avoids starting Demo with an arbitrary weak config. Warm-start is **Demo-only**; Live is unchanged and remains fully manual. See `warm_start` in config, `python run_bot.py warm-start status` / `warm-start run`, and [docs/INSTALL_AND_RUN_GUIDE.md](docs/INSTALL_AND_RUN_GUIDE.md).

**Canonical install and run workflow (Ubuntu):** See **[docs/INSTALL_AND_RUN_GUIDE.md](docs/INSTALL_AND_RUN_GUIDE.md)** for the exact installation steps and recommended run sequence (demo burn-in → promote-env → guarded live → evaluate/optimize/shadow/promote/rollback → incident stop).

## Demo / burn-in (Ubuntu CLI)

**Bybit Demo Trading** is the recommended burn-in path: create demo API keys from your mainnet account (Demo Trading). Set `BYBIT_ENV=demo`, `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET` in `.env`. Do not use testnet for demo.

```bash
# From repo root
chmod +x install.sh
./install.sh
source venv/bin/activate
python3 bootstrap_config.py   # prompts for demo keys, optionally live keys
# Edit config/config.yaml: set operating_mode: demo_research (or mode: paper, dry_run: false, burn_in_enabled: true, burn_in_phase: demo)
python3 run_bot.py validate
python3 run_bot.py show-runtime-mode   # shows operating_mode, selected_environment, automation_active
./scripts/start_testnet_burnin.sh   # starts demo burn-in (or run_bot.py run)
```

To run fully simulated (no exchange orders, only signal/decision logging): set `mode: dry_run` or `dry_run: true` in config. For **real Demo orders** (recommended burn-in), use `mode: paper`, `dry_run: false` (bootstrap default when you choose demo), and `BYBIT_ENV=demo`.

### 2. Bootstrap config (optional, if not done in install)

Prompts for: **demo** API key/secret (Bybit Demo Trading; from mainnet account), optionally **live** key/secret (dual-key), default BYBIT_ENV (demo/live/testnet). Writes `.env` with `BYBIT_ENV`, `BYBIT_DEMO_API_KEY/SECRET`, `BYBIT_LIVE_API_KEY/SECRET`. Copy `.env.example` for variable names.

```bash
python3 bootstrap_config.py
```

### 3. Run

```bash
source venv/bin/activate
python3 run_bot.py run
```

## Project Structure

```
src/
  main.py           # Entry, run loop
  config/           # Config loading
  exchange/         # Bybit V5 client
  data/             # Universe, market state, features
  signals/          # Flow impulse scoring
  portfolio/        # Position manager
  risk/             # Risk engine
  execution/        # Order execution
  storage/          # SQLite persistence
  backtest/         # Replay/backtest
  utils/
tests/
config/
scripts/
```

## Configuration

- **Single-instance:** `config/config.yaml` – strategy, risk, execution, etc. `.env` – API keys.
- **Dual-instance:** Use `config/config.demo.yaml` + `.env.demo` for the Demo instance and `config/config.live.yaml` + `.env.live` for the Live instance. Paths (DB, artifacts, logs) are then scoped per instance (e.g. `data/demo/bot.db`, `artifacts/demo/`, `logs/demo/` and `data/live/bot.db`, `artifacts/live/`, `logs/live/`).
- `.env` – API keys (never commit). **Dual-key (recommended):** `BYBIT_ENV=demo|live`, `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET`, `BYBIT_LIVE_API_KEY`, `BYBIT_LIVE_API_SECRET`. Legacy: `BYBIT_API_KEY`, `BYBIT_API_SECRET` or `BYBIT_ENV=testnet` with testnet keys.

See `config/config.yaml.example` for all options. Key additions:
- **dry_run**: When true, no orders are placed (simulated entries only). When false, real orders are placed on the selected environment (Demo or Live). Bootstrap sets `dry_run: false` for real Demo burn-in.
- **Context refresh**: `kline_refresh_seconds`, `oi_refresh_seconds`, `funding_refresh_seconds`, `context_staleness_seconds`
- **Safety**: `max_daily_realized_loss_usdt`, `max_trades_per_hour`, `api_error_threshold`, `reentry_cooldown_seconds`
- **Recovery**: `recover_orphan_positions`, `emergency_flatten_on_startup`, `rest_reconciliation_interval_seconds`

## Scripts

- `scripts/paper_trade.sh` – Paper mode (testnet)
- `scripts/live_trade.sh` – Live (mainnet)
- `scripts/check_health.sh` – Health check
- **Operator workflow (burn-in):** `scripts/validate_env.sh`, `scripts/start_testnet_burnin.sh` (demo burn-in), `scripts/check_burnin.sh`, `scripts/check_small_live_ready.sh`, **`scripts/promote_demo_to_live.sh`** (promote-env), `scripts/start_small_live.sh`, `scripts/incident_stop.sh`, `scripts/generate_burnin_bundle.sh`, `scripts/show_runtime_mode.sh`, `scripts/backup_config.sh`, `scripts/operator_menu.sh`
- **Dual-instance:** `scripts/start_demo_research.sh`, `scripts/start_live_guarded.sh`, `scripts/status_demo.sh`, `scripts/status_live.sh`, `scripts/tail_logs.sh demo|live`
- **Systemd:** `scripts/install_systemd.sh` (main bot + optional automation timer; use `--dual-instance` for demo + live + demo-automation units), `scripts/service_status.sh`, `scripts/tail_logs.sh`, `scripts/automation_status.sh`

See **docs/INSTALL_AND_RUN_GUIDE.md** for the canonical install and run workflow. See **docs/BURN_IN_OPERATOR_RUNBOOK.md** for the full operator runbook (burn-in, promote-env, guarded live, evaluate, optimize, shadow, promote/rollback).

## Systemd

**Single-instance:** One main trading bot and an optional Demo orchestration timer. **Dual-instance (recommended):** Separate units for Demo and Live so both can run at once; only the Demo instance has the automation timer.

| Setup | Units |
|------|--------|
| Single | `money-flow-momentum.service`, `money-flow-momentum-automation.service` + `.timer` (optional) |
| Dual | `money-flow-momentum-demo.service`, `money-flow-momentum-live.service`, `money-flow-momentum-demo-automation.service` + `.timer` |

Trading and orchestration are separate; the timer does not start the bot.

```bash
# Single-instance
./scripts/install_systemd.sh   # install main service + automation timer (use --no-automation to skip timer)

# Dual-instance (Demo + Live + Demo automation)
./scripts/install_systemd.sh --dual-instance

# Edit User, Group, WorkingDirectory in generated units if needed
sudo systemctl daemon-reload

# Single: main bot (trading)
sudo systemctl enable money-flow-momentum
sudo systemctl start money-flow-momentum

# Single: automation timer (Demo orchestration; every 15 min)
sudo systemctl enable money-flow-momentum-automation.timer
sudo systemctl start money-flow-momentum-automation.timer

# Dual: Demo research instance
sudo systemctl enable money-flow-momentum-demo
sudo systemctl start money-flow-momentum-demo
sudo systemctl enable money-flow-momentum-demo-automation.timer
sudo systemctl start money-flow-momentum-demo-automation.timer

# Dual: Live guarded instance
sudo systemctl enable money-flow-momentum-live
sudo systemctl start money-flow-momentum-live

./scripts/service_status.sh              # both bot and automation (single) or use instance scripts
./scripts/status_demo.sh                 # dual: demo status
./scripts/status_live.sh                # dual: live status
./scripts/tail_logs.sh [lines]          # single: bot log (logs/bot.log)
./scripts/tail_logs.sh demo [lines]      # dual: demo log (logs/demo/)
./scripts/tail_logs.sh live [lines]     # dual: live log (logs/live/)
./scripts/tail_logs.sh "demo automation" [lines]  # dual: demo automation journal
./scripts/automation_status.sh          # automation status + recommendation (point at demo artifacts when dual)
```

To install only the main bot service (no automation timer): `./scripts/install_systemd.sh --no-automation`. The automation timer is only meaningful when `automation.enabled` and `automation.demo_orchestration_enabled` are true in config and the main bot is running in Demo.

## Log Rotation

Logs go to `logs/bot.log` when using systemd. Add `/etc/logrotate.d/money-flow-momentum`:

```
/home/ubuntu/Money-flow-momentum/logs/*.log {
    daily
    rotate 7
    compress
    missingok
}
```

## Tests

```bash
pytest tests/ -v
```

Covers: config, features, risk sizing, signals, universe filter, order ID, context staleness, eligibility, lifecycle (breakeven, time stop), **Stage 3** (config versioning, evaluation metrics, walk-forward, guardrails, promotion eligibility), **Stage 4** (flow features, regime, threshold policy, scoring, cluster, exit refinements, evaluation stage4, optimizer params), **Stage 5** (risk budget, **candidate-set allocator**, exposure, strategy registry, fill model, **heartbeat write/read**, health/status/report CLI, portfolio metrics, **optimizer Stage 5 params**).

## Stage 3 CLI (Ubuntu / Linux)

From repo root (with venv activated). Shadow mode is **post-hoc** (decisions from stored data; no live parallel scoring).

```bash
# Config versioning
python3 run_bot.py config list
python3 run_bot.py config show <config_id>
python3 run_bot.py config show --config-id <config_id>
python3 run_bot.py config activate <config_id>
python3 run_bot.py config rollback
python3 run_bot.py config diff --from <id1> --to <id2>

# Evaluation
python3 run_bot.py evaluate --from-date 2025-01-01 --to-date 2025-01-31
python3 run_bot.py evaluate --config-id <id>
python3 run_bot.py evaluate --symbol BTCUSDT

# Optimization (includes Stage 5 params when stage5 enabled)
python3 run_bot.py optimize run --config-id <id>
python3 run_bot.py optimize run --from-date 2025-01-01 --to-date 2025-01-31 --n-samples 20
python3 run_bot.py optimize report <run_id>
python3 run_bot.py optimize report --run-id <run_id>

# Shadow (post-hoc: creates run, report from stored decisions)
python3 run_bot.py shadow start <candidate_config_id>
python3 run_bot.py shadow start --candidate-config-id <id>
python3 run_bot.py shadow stop --candidate-config-id <id>
python3 run_bot.py shadow report <candidate_config_id>

# Promotion / rollback
python3 run_bot.py promote --config-id <id>
python3 run_bot.py promote status
python3 run_bot.py rollback
python3 run_bot.py candidates list

# Cross-instance: import Demo candidate into Live (then optionally activate)
python3 run_bot.py promote-to-live --candidate-config-id <DEMO_CANDIDATE_ID> --demo-config config/config.demo.yaml --live-config config/config.live.yaml
python3 run_bot.py promote-to-live ... --activate   # to make it active in Live
python3 run_bot.py config show --config-id <live_imported_id> --config config/config.live.yaml

# Stage 5: health, status, report (heartbeat from runtime loops)
python3 run_bot.py health
python3 run_bot.py health --heartbeat artifacts/heartbeat.json --stale-sec 300
# Dual-instance: use instance-scoped paths, e.g. --config config/config.demo.yaml so heartbeat is artifacts/demo/heartbeat.json
python3 run_bot.py status
python3 run_bot.py status --heartbeat artifacts/heartbeat.json
python3 run_bot.py report
python3 run_bot.py report --heartbeat artifacts/heartbeat.json

# Burn-in validation
python3 run_bot.py burnin status
python3 run_bot.py burnin report --window 24
python3 run_bot.py burnin readiness
python3 run_bot.py burnin readiness --output artifacts/burnin --window 24

# Demo automation (optional; Demo only)
python3 run_bot.py automation cycle
python3 run_bot.py automation status

# Environment/config validation (install and pre-run)
python3 run_bot.py validate
python3 run_bot.py show-runtime-mode
```

See **docs/STAGE3_ADAPTIVE_FRAMEWORK.md**, **docs/OPTIMIZATION_WORKFLOW.md**, **docs/PROMOTION_AND_ROLLBACK.md**, **docs/STAGE4_STRATEGY_REFINEMENT.md**, **docs/REGIME_FILTERS_AND_THRESHOLDS.md**, **docs/STAGE5_PLATFORM_AND_PORTFOLIO.md**, **docs/MONITORING_AND_ALERTING.md**, **docs/DEPLOYMENT_AND_HEALTHCHECKS.md**, **docs/BURN_IN_AND_LIVE_VALIDATION.md**, **docs/BURN_IN_OPERATOR_RUNBOOK.md**.

## Requirements

- Python 3.11+
- Ubuntu 20.04+ (or similar Linux)

## License

MIT
