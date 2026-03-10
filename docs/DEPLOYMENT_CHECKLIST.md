# Deployment Checklist

## Pre-Deploy

- [ ] Python 3.11+ installed
- [ ] `install.sh` run successfully
- [ ] `bootstrap_config.py` completed (or `.env` + `config/config.yaml` present)
- [ ] `.env` has valid API keys
- [ ] `config/config.yaml` reviewed (mode=paper or dry_run for testnet)
- [ ] Testnet tested first (BYBIT_TESTNET=true)

## Config

- [ ] `score_interval_seconds`, `kline_refresh_seconds`, `oi_refresh_seconds`, `funding_refresh_seconds` set
- [ ] `context_staleness_seconds` and `public_ws_max_symbols_per_connection` set
- [ ] `recover_orphan_positions`, `emergency_flatten_on_startup` as desired
- [ ] Kill switch limits: `max_daily_drawdown_pct`, `max_daily_realized_loss_usdt`

## Server Setup

- [ ] Ubuntu 20.04+ (or compatible)
- [ ] `data/`, `config/`, `logs/` exist
- [ ] Log rotation configured
- [ ] systemd service installed (if using)

## Go-Live

- [ ] Switch `BYBIT_TESTNET=false` in .env
- [ ] Set `mode: live` in config
- [ ] Verify API key has trading permissions (no withdrawal)
- [ ] Start with small `risk_per_trade_pct` (e.g. 0.25%)
- [ ] Monitor logs for first hour

## Post-Deploy

- [ ] `scripts/check_health.sh` passes
- [ ] DB file growing (signals, entry_decisions, lifecycle_events)
- [ ] No repeated errors in logs
- [ ] Private WS and REST reconciliation both active
