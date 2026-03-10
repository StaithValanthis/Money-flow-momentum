# Optimization Workflow

## Overview

The optimizer uses **bounded** parameter search (grid or random) over **approved** parameters only. It does not allow every config field to change.

## Approved Parameters

Only these paths may be modified by the optimizer / candidate generation:

- `entry.long_threshold`, `entry.short_threshold`, `entry.min_delta_1m`
- `entry.min_buy_sell_ratio_long`, `entry.max_buy_sell_ratio_short`
- **Stage 4**: `entry.use_adaptive_thresholds`, `entry.use_regime_filter`, `entry.regime_block_trend`, `entry.regime_block_chop`
- **Stage 4**: `entry.anti_chase_penalty`, `entry.persistence_bonus`, `entry.max_positions_per_cluster`
- `score_weights.*` (w1–w7)
- `stop_tp.atr_multiplier_sl`, `tp1_r_multiple`, `tp2_r_multiple`, `tp1_pct`, `tp2_pct`
- `stop_tp.trailing_stop_atr_multiple`, `time_stop_bars`
- **Stage 4**: `stop_tp.exhaustion_exit_enabled`, `stop_tp.exhaustion_flow_price_ratio_max`, `stop_tp.failed_breakout_exit_enabled`, `stop_tp.failed_breakout_reversal_pct`, `stop_tp.volatility_aware_time_stop`, `stop_tp.time_stop_vol_multiplier`
- `risk.risk_per_trade_pct`, `reentry_cooldown_seconds`, `symbol_cooldown_after_stop_seconds`
- **Stage 5**: `risk.allocation_method` (discrete: equal_risk, score_weighted, capped_score_weighted, cluster_aware), `risk.max_cluster_risk_pct`, `risk.max_long_risk_pct`, `risk.max_short_risk_pct`, `portfolio_exposure.max_gross_exposure_per_cluster_pct`, `portfolio_exposure.max_risk_per_cluster_pct`, `portfolio_exposure.same_direction_concentration_penalty_pct` (all with conservative bounds)

The parameter space is `get_bounded_space(stage4=True, stage5=True)` by default. Stage 5 params are bounded and safe; optimization run summaries include `stage5_params_included: true` when they are in use.

## Walk-Forward Splits

- Single or multiple segments over a time range.
- Each segment: **train** → **validation** → **test** (or train/val only).
- Metrics are computed in-sample (train) and out-of-sample (validation/test).

## Objective

Composite score rewards:

- Return / expectancy
- Penalizes: drawdown, instability (e.g. negative Sharpe-like), low trade count.

## Guardrails

Candidates can be **rejected** or **penalized** if:

- Trade count too low (configurable min).
- Out-of-sample return degrades materially vs in-sample.
- Drawdown worse than baseline.
- Improvement vs baseline too small.
- One symbol dominates PnL (concentration).

## Replay Limitations

- Replay uses **stored** signal snapshots and entry decisions; it does not re-run the live market stream.
- Not tick-accurate; useful for comparison and analysis, not for exact PnL simulation.

## CLI

```bash
# Run optimization (uses active or --config-id as baseline)
python3 run_bot.py optimize run --config-id <id> --from-date 2025-01-01 --to-date 2025-01-31 --n-samples 20

# Show run summary
python3 run_bot.py optimize report <run_id>
```

Best candidate is written to `candidate_configs` and an artifact under `artifacts/configs/`. Promote only after shadow and evaluation.
