# Deployment Checklist

## Pre-Deploy

- [ ] Python 3.11+ installed
- [ ] `install.sh` run successfully
- [ ] `bootstrap_config.py` completed
- [ ] `.env` has valid API keys
- [ ] `config/config.yaml` reviewed
- [ ] Testnet tested first

## Server Setup

- [ ] Ubuntu 20.04+ (or compatible)
- [ ] `data/`, `config/`, `logs/` exist
- [ ] Log rotation configured
- [ ] systemd service installed (if using)

## Go-Live

- [ ] Switch `BYBIT_TESTNET=false` in .env
- [ ] Set `mode: live` in config
- [ ] Verify API key has trading permissions
- [ ] Start with small `risk_per_trade_pct` (e.g. 0.25%)
- [ ] Monitor logs for first hour

## Post-Deploy

- [ ] `scripts/check_health.sh` passes
- [ ] DB file growing (trades, signals)
- [ ] No repeated errors in logs
