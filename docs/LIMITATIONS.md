# Known Limitations and Next Improvements

## Current Limitations

1. **Kline format**: Bybit returns list of lists for klines; instrument refresh may re-fetch full universe and change symbol set during run
2. **Correlation filter**: max_correlation_positions not implemented; Stage 4 adds **cluster control** (max positions per cluster via correlation proxy), not full correlation matrix
3. **Backtest**: Uses saved trades only; no full replay from trade stream
4. **Stage 3 replay**: Replay uses stored observations; not tick-accurate; no full market re-simulation
5. **Shadow**: Shadow runner records decisions in DB; comparison is **post-hoc** from stored data (no in-process live parallel candidate scoring). Use `shadow start` / `shadow stop --candidate-config-id` / `shadow report` to create runs and generate comparison reports.
6. **Degradation monitor**: Requires a minimum number of trades (default 5) in the evaluation window; otherwise no check is performed and status is "insufficient data".
7. **Stage 4**: Regime and clustering use single-snapshot or short-window proxies; evaluation by exit reason uses event counts (no direct PnL per exit reason without schema change). Optimizer samples params over same stored data unless backtest-per-candidate is added.
8. **Stage 5**: Allocator operates on a **candidate set** (multiple candidates per cycle; higher score preferred when budgets are tight). Exposure controls are lightweight. Heartbeat is **written by runtime loops** (context refresh, WS, reconciliation, lifecycle, score/entry, degradation monitor). Replay/backtest uses stored trades + fill model (not tick-accurate). Evaluation includes resized_by_allocation and allocation_method_usage counts.
9. **Burn-in**: Execution audit and protection audit are best-effort; readiness classification is conservative and heuristic. No automatic escalation of phase; operator must adjust config.

## Next Improvements

1. **Correlation**: Stage 4 cluster control uses correlation proxy; optional full rolling correlation matrix
2. **Backtest**: Replay from saved trade stream or kline aggregates
3. **PostgreSQL**: Optional storage backend
4. **Prometheus**: Metrics for observability
