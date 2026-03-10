# Regime Filters and Adaptive Thresholds

## Regime Filters

Regimes are derived from existing features (ATR, realized vol, price persistence, flow–price alignment, breakout/failed-breakout scores).

### Regime dimensions

- **Trend vs chop** – Trend: price response to flow positive and meaningful return. Chop: CVD divergence or small return despite flow.
- **Vol regime** – Low/high vol from ATR percentile; expansion/compression from volatility expansion ratio.
- **Momentum vs mean-reversion** – Momentum: breakout confirmation score high. Mean-reversion: failed breakout score high.

### Config

- **`entry.use_regime_filter`** – When true, regime is used to allow/block entries.
- **`entry.regime_block_chop`** – When true, block entries in chop regime.
- **`entry.regime_block_trend`** – When true, block entries in trend regime (conservative).

Regime label is stored per signal in `json_features.regime_label` (e.g. `trend_high_vol_momentum`) and used in evaluation stratification.

## Adaptive Thresholds

Symbols are bucketed by:

- **Liquidity** – Proxy: trade_count_1m × last_price (percentiles 33/66 → low/mid/high).
- **Volatility** – ATR as % of price (percentiles 33/66 → low/mid/high).

### Policy

- **Low liquidity** – Stricter max spread (base minus penalty bps).
- **High volatility** – Higher long threshold, lower short threshold (stricter score gate).

Profile name is like `low_mid`, `mid_high`, etc., and stored in `json_features.threshold_profile`.

### Config

- **`entry.use_adaptive_thresholds`** – When true, per-symbol profile is computed and applied; otherwise base entry thresholds are used for all.

## Usage

1. Enable in config: `stage4_enabled: true`, then `use_regime_filter` and/or `use_adaptive_thresholds` as desired.
2. Run the bot; regime and threshold profile appear in signal snapshots and evaluation Stage 4 metrics.
3. Optimizer can search over `entry.use_adaptive_thresholds`, `entry.use_regime_filter`, and related Stage 4 params (see APPROVED_PARAM_PATHS and parameter_space).

## Evaluation

- **Regime** – Counts and (where available) metrics by `regime_label`.
- **Threshold profile** – Counts by `threshold_profile`.
- **Exit reason** – Lifecycle event counts by `exit_reason` (max_hold, flow_reversal, exhaustion, failed_breakout, etc.).

Reports are written to the evaluation artifact dir (JSON, CSV, Markdown).
