# Architecture Summary

## Overview

Money Flow Momentum is a cross-sectional trading bot that:

1. Discovers the Bybit linear USDT perpetual universe dynamically
2. Consumes real-time public trade stream per symbol
3. Computes flow metrics (delta, CVD, buy/sell ratio, VWAP)
4. Scores symbols and ranks long/short candidates
5. Executes with risk management (ATR stops, TP targets, kill switch)

## Data Flow

```
Bybit WS (publicTrade) → MarketStateManager → FeatureBuilder → FlowImpulseScorer
                                                                    ↓
Bybit REST (tickers, OI, funding) ─────────────────────────────→ Signals
                                                                    ↓
RiskEngine + PositionManager → Executor → Bybit REST (place order)
```

## Components

| Module | Responsibility |
|--------|----------------|
| `exchange/bybit_client` | REST + WS, rate limit, retry |
| `data/universe` | Fetch instruments, filter by liquidity/spread |
| `data/market_state` | Rolling trade buffers, aggregates |
| `data/feature_builder` | State → features |
| `signals/flow_impulse` | Z-score cross-sectional ranking |
| `risk/risk_engine` | Position sizing, kill switch |
| `execution/executor` | Place orders, TP/SL |
| `portfolio/position_manager` | Track positions, cooldowns |
| `storage/db` | SQLite audit trail |

## Signal Logic

Score = w1·z(δ1m) + w2·z(cvd_slope) + w3·z(buy_sell_ratio) + w4·z(return) + w5·z(OI_change) − w6·z(spread) − w7·funding_penalty

Long: score > threshold, δ1m > 0, ratio > 1.05, spread OK, OI non-negative.
Short: inverse.

## Deployment

- Single process, main thread + WS thread
- Graceful shutdown on SIGTERM/SIGINT
- systemd for production
