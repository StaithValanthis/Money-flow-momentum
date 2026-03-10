# Money Flow Momentum

Production-ready Bybit V5 cross-sectional flow impulse trading bot for linear USDT perpetuals.

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

**Canonical install and run workflow (Ubuntu):** See **[docs/INSTALL_AND_RUN_GUIDE.md](docs/INSTALL_AND_RUN_GUIDE.md)** for the exact installation steps and recommended run sequence (demo burn-in → promote-env → guarded live → evaluate/optimize/shadow/promote/rollback → incident stop).

## Demo / burn-in (Ubuntu CLI)

**Bybit Demo Trading** is the recommended burn-in path: create demo API keys from your mainnet account (Demo Trading). Set `BYBIT_ENV=demo`, `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET` in `.env`. Do not use testnet for demo.

```bash
# From repo root
chmod +x install.sh
./install.sh
source venv/bin/activate
python3 bootstrap_config.py   # prompts for demo keys, optionally live keys
# Edit config/config.yaml: mode: paper, burn_in_enabled: true, burn_in_phase: demo
python3 run_bot.py validate
python3 run_bot.py show-runtime-mode
./scripts/start_testnet_burnin.sh   # starts demo burn-in (or run_bot.py run)
```

To run fully dry (no exchange orders, only signal/decision logging): set `mode: dry_run` in config.

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

- `config/config.yaml` – strategy, risk, execution, context refresh, WS, recovery
- `.env` – API keys (never commit). **Dual-key (recommended):** `BYBIT_ENV=demo|live`, `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET`, `BYBIT_LIVE_API_KEY`, `BYBIT_LIVE_API_SECRET`. Legacy: `BYBIT_API_KEY`, `BYBIT_API_SECRET` or `BYBIT_ENV=testnet` with testnet keys.

See `config/config.yaml.example` for all options. Key additions:
- **dry_run / demo_mode**: Same decision path, no live orders when true
- **Context refresh**: `kline_refresh_seconds`, `oi_refresh_seconds`, `funding_refresh_seconds`, `context_staleness_seconds`
- **Safety**: `max_daily_realized_loss_usdt`, `max_trades_per_hour`, `api_error_threshold`, `reentry_cooldown_seconds`
- **Recovery**: `recover_orphan_positions`, `emergency_flatten_on_startup`, `rest_reconciliation_interval_seconds`

## Scripts

- `scripts/paper_trade.sh` – Paper mode (testnet)
- `scripts/live_trade.sh` – Live (mainnet)
- `scripts/check_health.sh` – Health check
- **Operator workflow (burn-in):** `scripts/validate_env.sh`, `scripts/start_testnet_burnin.sh` (demo burn-in), `scripts/check_burnin.sh`, `scripts/check_small_live_ready.sh`, **`scripts/promote_demo_to_live.sh`** (promote-env), `scripts/start_small_live.sh`, `scripts/incident_stop.sh`, `scripts/generate_burnin_bundle.sh`, `scripts/show_runtime_mode.sh`, `scripts/backup_config.sh`, `scripts/operator_menu.sh`
- **Systemd:** `scripts/install_systemd.sh`, `scripts/service_status.sh`, `scripts/tail_logs.sh`

See **docs/INSTALL_AND_RUN_GUIDE.md** for the canonical install and run workflow. See **docs/BURN_IN_OPERATOR_RUNBOOK.md** for the full operator runbook (burn-in, promote-env, guarded live, evaluate, optimize, shadow, promote/rollback).

## Systemd

```bash
./scripts/install_systemd.sh   # install unit (prompts for repo path / user)
# Or copy and edit manually:
sudo cp money-flow-momentum.service /etc/systemd/system/
# Edit User, Group, WorkingDirectory, ExecStart, StandardOutput path if needed
sudo systemctl daemon-reload
sudo systemctl enable money-flow-momentum
sudo systemctl start money-flow-momentum
./scripts/service_status.sh
./scripts/tail_logs.sh
```

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

# Stage 5: health, status, report (heartbeat from runtime loops)
python3 run_bot.py health
python3 run_bot.py health --heartbeat artifacts/heartbeat.json --stale-sec 300
python3 run_bot.py status
python3 run_bot.py status --heartbeat artifacts/heartbeat.json
python3 run_bot.py report
python3 run_bot.py report --heartbeat artifacts/heartbeat.json

# Burn-in validation
python3 run_bot.py burnin status
python3 run_bot.py burnin report --window 24
python3 run_bot.py burnin readiness
python3 run_bot.py burnin readiness --output artifacts/burnin --window 24

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
