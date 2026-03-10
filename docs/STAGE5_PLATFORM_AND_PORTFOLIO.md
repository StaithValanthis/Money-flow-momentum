# Stage 5: Platform and Portfolio

Stage 5 upgrades the bot into a more **portfolio-aware, operationally robust, production-grade** platform while preserving the existing strategy and Stage 3/4 framework.

## What Stage 5 Adds

### 1. Portfolio Risk Budgeting
- **Risk budget state**: Total, long, short, and per-cluster risk tracked from current positions.
- **Budget checks**: `max_total_risk_pct`, optional `max_long_risk_pct`, `max_short_risk_pct`, `max_cluster_risk_pct`.
- **Allocator**: Operates on a **candidate set**: after scoring, all valid entry candidates are collected; allocation runs once over the set. Methods: `equal_risk`, `score_weighted`, `capped_score_weighted`, `cluster_aware`. Higher-score candidates are processed first; budgets are applied across the set; sizes may be reduced or candidates blocked when budgets are tight. Single-candidate behavior is unchanged.
- Rejections (e.g. `total_risk_budget`, `long_risk_budget`, `cluster_risk_budget`) are logged in entry_decisions as `rejected:stage5:...`. Placed orders record allocation reason (e.g. `order_placed:resized`, `order_placed:capped_score_weighted`) for evaluation.

### 2. Exposure Controls
- **PortfolioExposureConfig**: `max_gross_exposure_per_cluster_pct`, `max_risk_per_cluster_pct`, `max_correlated_positions`, `same_direction_concentration_penalty_pct`.
- Cluster/exposure checks integrate with entry and allocation; rejection reasons persisted.

### 3. Multi-Strategy Readiness
- **BaseStrategy**: Interface with `build_features`, `score_candidates`, `evaluate_entry`, optional `manage_position`.
- **Registry**: `get_strategy(name, config)`, `list_strategies()`. Config field `active_strategy` (default `flow_impulse`).
- **FlowImpulseStrategy**: Wraps existing FeatureBuilder + FlowImpulseScorer; evaluation/optimizer can use strategy identity.

### 4. Replay / Backtest Realism
- **FillModelConfig**: Slippage bps, spread cost bps, partial fill pct, entry/exit delay bars.
- **fill_result()**: Applies slippage and spread cost to a fill.
- **BacktestReport**: Replay trades with fill assumptions; write JSON + Markdown with limitations stated.

### 5. Monitoring and Alerting
- **HealthSnapshot**: Per-loop status (ok/stale/fail), `report_ok` / `report_fail` / `report_stale`; optional `set_meta(config_id, strategy)` for heartbeat.
- **Heartbeat**: Written by **actual runtime loops** every ~30s from the score/entry loop; loops that call `report_ok` include: public_ws, private_ws, context_refresh, reconciliation, lifecycle, score_entry, degradation_monitor. File: `artifacts/heartbeat.json`.
- **write_heartbeat(health, path)** / **read_heartbeat(path)** for persistence and CLI consumption.
- **AlertRouter**: Log + optional file + optional webhook; config-driven.

### 6. Storage / Artifacts
- **artifacts.py**: Central `artifacts_root`, `evaluations_dir`, `optimizations_dir`, etc.; `ensure_artifact_dirs()`, `write_manifest()`.
- **archive.py**: Simple rotation (keep_latest, max_age_seconds) for evaluation/optimization/shadow dirs.
- **DB**: WAL mode and SYNCHRONOUS=NORMAL for SQLite robustness.

### 7. Deployment / CLI
- **health**: Reads heartbeat; reports per-loop freshness and age; marks loops stale when last_ok older than `--stale-sec` (default 300); exits 1 if heartbeat missing, stale, or any loop fail/stale. Use `--heartbeat path` to point to file.
- **status**: Active config, DB path, stage5/strategy, artifact dirs, **last heartbeat age and per-loop freshness** when heartbeat file exists.
- **report**: Summary of active config, degradation count (24h), recent promotions, **loop health / stale summary** from heartbeat; states "No heartbeat file" when missing.

### 8. Evaluation
- **Stage 5 portfolio metrics**: `stage5_rejection_counts`, `cluster_block_count`, `budget_block_count`, **resized_by_allocation_count**, **allocation_method_usage** from entry_decisions (reason field).
- Written to evaluation summary and report Markdown.

## Configuration

- **risk**: `max_long_risk_pct`, `max_short_risk_pct`, `max_cluster_risk_pct`, `allocation_method`.
- **portfolio_exposure**: `max_gross_exposure_per_cluster_pct`, `max_risk_per_cluster_pct`, `max_correlated_positions`, `same_direction_concentration_penalty_pct`.
- **stage5_enabled**: Master switch (default true).
- **active_strategy**: Strategy name (default `flow_impulse`).

## Optimizer (Stage 5 parameter space)

When the optimizer runs, the bounded parameter space can include **Stage 5-safe** params (see docs/OPTIMIZATION_WORKFLOW.md):

- **risk.allocation_method** (discrete: equal_risk, score_weighted, capped_score_weighted, cluster_aware)
- **risk.max_cluster_risk_pct**, **risk.max_long_risk_pct**, **risk.max_short_risk_pct** (bounded 0–6%)
- **portfolio_exposure.max_gross_exposure_per_cluster_pct**, **portfolio_exposure.max_risk_per_cluster_pct**, **portfolio_exposure.same_direction_concentration_penalty_pct** (conservative bounds)

Optimization run summaries include `stage5_params_included: true` when these are in use.

## Limitations

- Exposure controls are lightweight (no full correlation matrix).
- Replay/backtest uses stored trades + fill model; not tick-accurate.
- Optimizer Stage 5 params are bounded and conservative; no broad operational controls exposed.
