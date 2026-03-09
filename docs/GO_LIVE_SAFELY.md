# Go Live Safely Checklist

## Before Going Live

1. **Test on testnet** for at least 24–48 hours
2. **Verify** universe filters (e.g. min turnover) exclude illiquid symbols
3. **Start small**: risk_per_trade_pct = 0.25%, max_concurrent_positions = 2
4. **API keys**: Use keys with trading only; no withdrawal permissions

## First Live Run

1. Run in `paper` mode first (no real orders) to confirm signals
2. Switch to `live` only when confident
3. Monitor for 1–2 hours before leaving unattended

## Risk Controls

- Kill switch enabled (default)
- Max daily drawdown set (e.g. 5%)
- Stale data shutdown (60s default)
- Cooldown after loss

## Emergency

- Stop: `sudo systemctl stop money-flow-momentum` or Ctrl+C
- Flatten: Implement emergency_flatten command (executor has method)
- Check positions on Bybit UI
