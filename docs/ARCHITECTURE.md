# Architecture Summary

## Overview

Money Flow Momentum is a cross-sectional trading bot that:

1. Discovers the Bybit linear USDT perpetual universe dynamically
2. Consumes real-time public trade stream (sharded across multiple WS connections)
3. Refreshes context on schedule: klines, OI, funding, long/short ratio; blocks entries if context stale
4. Computes flow metrics (delta, CVD, buy/sell ratio, VWAP)
5. Scores symbols and ranks long/short candidates
6. Validates eligibility (universe, freshness, cooldown), risk gates, then sizes and places orders (or dry-run)
7. Reconciles orders/fills/positions via private WS + periodic REST
8. Manages lifecycle: breakeven after TP1, time stop, flow-reversal exit
9. Recovers on restart: rehydrate lifecycle from exchange positions, reattach protection if missing

## Data Flow

```
Bybit public WS (sharded) → MarketStateManager → FeatureBuilder
Bybit REST (klines, OI, funding, L/S ratio) → ContextRefresher → staleness gating
                                                                    ↓
Bybit private WS (order, position, execution) → ReconciliationStore → FlowImpulseScorer
                                                                    ↓
RiskEngine + PositionManager + check_eligibility → Executor → Bybit REST (place order)
LifecycleManager → breakeven / time stop / flow reversal → Executor (set_tp_sl / close_position)
```

## Components

| Module | Responsibility |
|--------|----------------|
| `exchange/bybit_client` | REST + public/private WS, rate limit, retry |
| `exchange/ws_shard` | Shard universe across multiple public WS connections |
| `data/universe` | Fetch instruments, filter by liquidity/spread |
| `data/context_refresher` | Scheduled kline/OI/funding/LS refresh; staleness timestamps |
| `data/eligibility` | Universe + context freshness + cooldown check |
| `data/market_state` | Rolling trade buffers, aggregates |
| `data/feature_builder` | State → features |
| `signals/flow_impulse` | Z-score cross-sectional ranking; Stage 4: regime/threshold/cluster, score components |
| `signals/regime_filters` | Regime classification (trend/chop, vol, momentum/mean-revert) |
| `signals/threshold_policy` | Adaptive thresholds by liquidity/volatility bucket |
| `risk/risk_engine` | Position sizing, kill switches, circuit breaker |
| `execution/executor` | Place orders, TP/SL, reduce-only close, TP1/TP2 exits |
| `portfolio/position_manager` | Track positions, cooldowns |
| `portfolio/lifecycle` | TP1/TP2/breakeven/time stop/flow reversal, trailing stop state; Stage 4: exhaustion/failed-breakout exit, vol-aware time stop |
| `portfolio/correlation` | Correlation proxy, cluster assignment, cluster_block for max positions per cluster |
| `portfolio/allocator` | Portfolio allocation (equal_risk, score_weighted, etc.); budget clipping |
| `portfolio/risk_budget` | Portfolio-level risk budget state and checks (long/short/cluster/total) |
| `portfolio/exposure_controls` | Cluster gross/risk exposure, same-direction concentration penalty |
| `strategies/base` | Strategy interface (build_features, score_candidates, evaluate_entry) |
| `strategies/registry` | Strategy registry; resolve active strategy by name |
| `strategies/flow_impulse_strategy` | Flow-impulse strategy implementing BaseStrategy |
| `research/fill_model` | Slippage and spread cost for replay/backtest |
| `research/backtest_runner` | Backtest report with fill assumptions and limitations |
| `monitoring/health` | Health snapshot per loop (ok/stale/fail) |
| `monitoring/alerts` | Alert routing (log, file, optional webhook) |
| `monitoring/heartbeat` | Persist/read health snapshot |
| `storage/artifacts` | Central artifact paths and manifest |
| `storage/archive` | Simple rotation for old artifacts |
| `storage/db` | SQLite: trades, signals, entry_decisions, lifecycle_events, kill_switch; Stage 3: config_versions, evaluation_reports, optimization_runs, shadow_*, promotion_events, rollback_events, degradation_events |
| `storage/reconciliation` | In-memory orders/fills/positions; REST sync |
| **Stage 3** | |
| `config/versioning` | Config registry, hash, status lifecycle, activate/stage/reject/rollback, diff |
| `config/candidate_factory` | Generate candidate from parent; approved params only; register + artifact |
| `evaluation/` | Evaluator, core/stratified/diagnostic metrics, reporting, datasets |
| `research/` | Dataset builder, replay engine (approximate) |
| `optimizer/` | Parameter space, walk-forward splits, objectives, guardrails, search |
| `shadow/` | Shadow runner, baseline vs candidate comparison |
| `promotion/` | Promotion rules, promoter, live degradation monitor |

## Signal Logic

Base: Score = w1·z(δ1m) + w2·z(cvd_slope) + w3·z(buy_sell_ratio) + w4·z(return) + w5·z(OI_change) − w6·z(spread) − w7·funding_penalty + divergence_bonus.

Stage 4 (when enabled): + persistence_bonus − anti_chase_penalty − exhaustion_penalty; adaptive long/short/spread thresholds by liquidity/vol bucket; regime filter and cluster block can set direction to none. Long/Short gates unchanged; regime and cluster can reject after gates.

## Deployment

- Single process, main thread + WS thread
- Graceful shutdown on SIGTERM/SIGINT
- systemd for production
