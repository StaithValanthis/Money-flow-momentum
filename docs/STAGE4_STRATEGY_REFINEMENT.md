# Stage 4: Strategy Refinement

Stage 4 improves the flow-impulse strategy’s **edge and alpha quality** without replacing the Stage 2/3 architecture. It keeps the same live path, config versioning, evaluation, optimizer, shadow, promotion, and rollback systems.

## What Stage 4 Adds

1. **Flow feature expansion** – Extra features from existing data: delta acceleration, CVD divergence/persistence, trade intensity burst, price response to flow, move efficiency, flow exhaustion, volatility expansion, breakout confirmation, failed breakout. All robust to missing data and logged in signal snapshots (via `json_features`).

2. **Regime filters** – Regime classification (trend vs chop, low/high vol, expansion/compression, momentum vs mean-reversion). Config can enable/disable regime filtering and block entries in selected regimes. Regime labels are stored in signal/evaluation data.

3. **Adaptive thresholds** – Threshold policy by liquidity and volatility bucket (stricter for illiquid/high-spread; different score gates by vol bucket). Config-driven; which profile was applied is logged.

4. **Improved ranking** – Composite scoring: base flow score + regime multiplier + liquidity/spread/crowding + persistence bonus + anti-chase penalty + exhaustion penalty. Score components are exposed and stored; simple fallback when Stage 4 is disabled.

5. **Exit logic refinements** – On top of existing stop/TP/breakeven/runner:
   - Volatility-aware time stop (optional scaling of max hold by vol)
   - Exhaustion-based early exit (strong flow, weak price follow-through)
   - Failed-breakout exit (price reversal)
   - All config-driven; exit reasons logged; evaluation can stratify by exit reason.

6. **Portfolio correlation / cluster controls** – Lightweight clustering (correlation proxy from returns/delta sign). Config: max positions per cluster; cluster block prevents stacking too many trades in the same micro-cluster.

7. **Evaluation expansion** – Metrics by regime, liquidity/volatility bucket, threshold profile, exit reason, Stage 4 rejection reasons. Written to JSON, CSV, and Markdown.

8. **Optimizer** – Bounded search over selected Stage 4 params (regime toggles, adaptive thresholds, anti-chase, persistence, exhaustion/failed-breakout exits, cluster caps). Guardrails unchanged; candidates can be rejected for low OOS trade count, degradation, etc.

9. **Shadow / promotion** – New config fields are versioned; evaluation and shadow reports can show regime, threshold profile, cluster, and ranking differences. Promotion rules do not ignore new metrics when present.

## Configuration

- **`stage4_enabled`** (default `true`): Master switch for Stage 4 scoring, regime, thresholds, cluster, and exit refinements.
- **Entry**: `use_adaptive_thresholds`, `use_regime_filter`, `regime_block_trend`, `regime_block_chop`, `anti_chase_penalty`, `persistence_bonus`, `max_positions_per_cluster`.
- **Stop/TP**: `exhaustion_exit_enabled`, `exhaustion_flow_price_ratio_max`, `failed_breakout_exit_enabled`, `failed_breakout_reversal_pct`, `volatility_aware_time_stop`, `time_stop_vol_multiplier`.

See **docs/REGIME_FILTERS_AND_THRESHOLDS.md** for regime and threshold behavior.

## Limitations / Caveats

- Regime and clustering use **single-snapshot** or short-window proxies (e.g. return/delta similarity); not full historical correlation.
- Evaluation “by exit reason” uses lifecycle event counts; linking exact PnL to exit reason would require storing exit_reason on the trade record.
- Optimizer currently samples params and evaluates on the **same** stored dataset; true backtest-per-candidate would require replay with each config.
- Cluster correlation is a simple proxy; not full portfolio optimization.

## Backward Compatibility

- With `stage4_enabled: false`, scoring and entries behave like Stage 3 (no regime/threshold/cluster, no new exit reasons).
- New fields in signal snapshots are in `json_features`; DB schema is unchanged.
- Config versioning and promotion/rollback are unchanged; new keys are part of the config hash when present.
