"""Backtest-style warm-start evaluation engine (Demo-only).

Runs the strategy replay on candles and applies basic fee/slippage costs to
produce more realistic trade PnL for candidate evaluation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.config.config import Config
from src.evaluation.datasets import compute_realized_pnl_by_pairing
from src.evaluation.metrics import compute_core_metrics
from src.utils.logging import get_logger
from src.warm_start.strategy_replay import replay_strategy_from_candles

log = get_logger(__name__)


def run_backtest_on_candles(
    config: Config,
    candles_by_symbol: Dict[str, List[Dict[str, Any]]],
    fee_bps: float,
    slippage_bps: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """
    Run backtest-style evaluation on candles for a given config.

    This wraps strategy replay and applies per-side fee + slippage in basis points
    to each round-trip (entry+exit), adjusting realized PnL accordingly.

    Returns (trades_with_costs, metrics, meta).
    """
    trades, replay_meta = replay_strategy_from_candles(config, candles_by_symbol)
    if not trades:
        metrics = compute_core_metrics([])
        meta = {
            "engine": "backtest_style",
            "symbols": list(sorted(candles_by_symbol.keys())),
            "trade_count": 0,
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
        }
        return trades, metrics, meta

    # Apply costs on exit rows by pairing with prior entry per symbol.
    per_side_bps = float(fee_bps or 0.0) + float(slippage_bps or 0.0)
    total_fee = 0.0
    total_slippage = 0.0

    # Maintain entry stacks per symbol for simple FIFO pairing.
    entry_stack: Dict[str, List[float]] = {}
    for t in trades:
        symbol = t.get("symbol") or ""
        if not symbol:
            continue
        link = (t.get("order_link_id") or "") or ""
        side = (t.get("side") or "").strip()
        price = float(t.get("price") or 0.0)
        if link == "entry":
            entry_stack.setdefault(symbol, []).append(price)
            continue
        if t.get("pnl") is None:
            continue
        # Exit row: pair with most recent entry for this symbol.
        if symbol not in entry_stack or not entry_stack[symbol]:
            continue
        entry_price = entry_stack[symbol].pop(0)
        # Approximate round-trip notional as average price * qty (qty=1.0 in replay).
        avg_price = (abs(entry_price) + abs(price)) / 2.0
        notional = avg_price * float(t.get("qty") or 1.0)
        cost_per_round_trip = per_side_bps / 10000.0 * 2.0 * notional
        # Split cost between fee and slippage halves for reporting.
        fee_part = float(fee_bps or 0.0) / max(per_side_bps, 1e-9) * cost_per_round_trip if per_side_bps > 0 else 0.0
        slippage_part = float(slippage_bps or 0.0) / max(per_side_bps, 1e-9) * cost_per_round_trip if per_side_bps > 0 else 0.0
        total_fee += fee_part
        total_slippage += slippage_part
        t["pnl"] = float(t.get("pnl") or 0.0) - cost_per_round_trip

    # Recompute realized PnL and metrics on cost-adjusted trades.
    paired = compute_realized_pnl_by_pairing(trades)
    metrics = compute_core_metrics(paired)
    metrics["fees_summary"] = total_fee
    metrics["slippage_summary"] = total_slippage

    meta = {
        "engine": "backtest_style",
        "symbols": list(sorted(candles_by_symbol.keys())),
        "trade_count": int(metrics.get("trade_count") or 0),
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
    }
    meta.update(replay_meta or {})
    return paired, metrics, meta

