"""Composite objective: return, drawdown penalty, instability, low-trade-count, turnover."""

from typing import Any


def composite_objective(
    metrics: dict[str, Any],
    return_weight: float = 0.4,
    drawdown_penalty_weight: float = 0.3,
    instability_penalty_weight: float = 0.1,
    low_trade_penalty_weight: float = 0.1,
    min_trades: int = 10,
    max_drawdown_cap_pct: float = 20.0,
) -> float:
    """
    Single score for optimizer. Higher is better.
    Penalizes drawdown, instability, and too few trades.
    """
    total_pnl = float(metrics.get("total_pnl") or 0)
    return_pct = float(metrics.get("return_pct") or 0)
    max_dd = float(metrics.get("max_drawdown") or 0)
    trade_count = int(metrics.get("trade_count") or 0)
    sharpe = float(metrics.get("sharpe_like") or 0)

    ret_component = return_pct / 10.0 if return_pct else 0
    ret_component = max(-5, min(5, ret_component))

    dd_penalty = 0.0
    if max_dd > max_drawdown_cap_pct:
        dd_penalty = (max_dd - max_drawdown_cap_pct) / 10.0
    dd_penalty += max_dd / 20.0

    instability = max(0, -sharpe) if sharpe < 0 else 0

    low_trade = 0.0
    if trade_count < min_trades:
        low_trade = (min_trades - trade_count) / min_trades * 2.0

    score = (
        return_weight * ret_component
        - drawdown_penalty_weight * dd_penalty
        - instability_penalty_weight * instability
        - low_trade_penalty_weight * low_trade
    )
    return score
