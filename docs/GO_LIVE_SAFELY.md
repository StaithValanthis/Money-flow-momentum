# Go Live Safely Checklist

## Before Going Live

1. **Test on testnet** for at least 24–48 hours with `mode: paper` or `dry_run`
2. **Verify** universe filters (e.g. min_24h_turnover_usdt) exclude illiquid symbols
3. **Start small**: risk_per_trade_pct = 0.25%, max_concurrent_positions = 2
4. **API keys**: Use keys with trading only; no withdrawal permissions

## Runtime

- **dry_run**: Same logic, no orders; use to validate signals and reject reasons in DB
- **paper**: Demo/testnet; orders can be placed on testnet if not dry_run
- **live**: Mainnet; real orders

## Safety Gates (all in config)

- Kill switch: daily drawdown %, daily realized loss USDT
- Stale data: public/private WS timeouts; context staleness blocks new entries
- Max positions per side, max portfolio notional, max trades per hour, API error circuit breaker

## Recovery

- On restart: REST sync of positions; lifecycle rehydrated; missing TP/SL can be reattached if `recover_orphan_positions` true
- Optional `emergency_flatten_on_startup`: close all positions on start (use with caution)

## Emergency

- Stop: `sudo systemctl stop money-flow-momentum` or Ctrl+C
- Flatten: set `emergency_flatten_on_startup: true` and restart, or close positions manually on Bybit UI
- Inspect: `data/bot.db` (entry_decisions, kill_switch_events, lifecycle_events)
