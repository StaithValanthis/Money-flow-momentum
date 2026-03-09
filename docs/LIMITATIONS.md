# Known Limitations and Next Improvements

## Current Limitations

1. **WebSocket symbol limit**: 50 symbols per subscription; universe may be larger
2. **Kline data**: Fetched via REST; not yet integrated into main loop for ATR/returns
3. **OI / funding**: REST poll not yet in scan loop; features may be stale
4. **Correlation filter**: max_correlation_positions not implemented (no correlation matrix)
5. **Live execution**: _execute_signals is simplified; full flow (size, place, TP/SL) needs wiring
6. **Backtest**: Uses saved trades only; no full replay from trade stream

## Next Improvements

1. **Multi-WS connections**: Split universe across multiple WS connections
2. **Kline integration**: Periodic REST fetch for 1m klines → feature builder
3. **OI/funding refresh**: Add to scan loop (batch REST)
4. **Correlation**: Compute rolling correlation, cap similar positions
5. **Full execution flow**: Wire risk sizing → executor → position manager
6. **Backtest**: Replay from saved trade stream or kline aggregates
7. **PostgreSQL**: Optional storage backend
8. **Metrics**: Prometheus/StatsD for observability
