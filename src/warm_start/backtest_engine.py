"""Backtest-style warm-start evaluation engine (Demo-only).

Runs protection-aware historical simulation: same entry logic as strategy replay,
but exits are simulated using OHLC intrabar stop/TP/time/signal logic so that
warm-start viability is closer to real Demo behavior.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from src.config.config import Config
from src.evaluation.datasets import compute_realized_pnl_by_pairing
from src.evaluation.metrics import compute_core_metrics
from src.utils.logging import get_logger
from src.warm_start.strategy_replay import _build_symbol_features_from_candles
from src.signals.flow_impulse import FlowImpulseScorer
from src.data.feature_builder import SymbolFeatures

log = get_logger(__name__)

# Canonical exit reasons for protection-aware simulation (used in order_link_id and exit_reason).
EXIT_STOP_LOSS = "stop_loss"
EXIT_TP1 = "tp1"
EXIT_TP2 = "tp2"
EXIT_BREAKEVEN_STOP = "breakeven_stop"
EXIT_TIME_STOP = "time_stop"
EXIT_MAX_HOLD = "max_hold"
EXIT_OPPOSITE_SIGNAL = "opposite_signal"
EXIT_FLOW_REVERSAL = "flow_reversal"


@dataclass
class _OpenPosition:
    symbol: str
    side: str
    entry_ts: int
    entry_price: float
    atr_at_entry: float
    bars_held: int = 0


def _bar_interval_ms(candles_by_symbol: Dict[str, List[Dict[str, Any]]]) -> int:
    """Infer bar interval in ms from first symbol's candles."""
    for candles in candles_by_symbol.values():
        if len(candles) >= 2:
            return int(candles[1]["start_ts"] - candles[0]["start_ts"])
    return 300_000  # default 5m


def _protection_levels(
    entry_price: float,
    atr: float,
    side: str,
    atr_mult_sl: float,
    tp1_r: float,
    tp2_r: float,
) -> Tuple[float, float, float]:
    """Return (stop_price, tp1_price, tp2_price) for the given side."""
    if side == "Buy":
        stop_price = entry_price - atr * atr_mult_sl
        tp1_price = entry_price + atr * tp1_r
        tp2_price = entry_price + atr * tp2_r
    else:
        stop_price = entry_price + atr * atr_mult_sl
        tp1_price = entry_price - atr * tp1_r
        tp2_price = entry_price - atr * tp2_r
    return stop_price, tp1_price, tp2_price


# Intrabar precedence (documented): we assume stop is checked first, then TP2, then TP1,
# then time_stop, then signal/max_hold. This is conservative (stop first) and deterministic.
def _intrabar_exit_long(
    low: float,
    high: float,
    close: float,
    stop_price: float,
    tp1_price: float,
    tp2_price: float,
) -> Tuple[str, float]:
    """Return (exit_reason, exit_price) for a long position. Precedence: stop, TP2, TP1."""
    if low <= stop_price:
        return EXIT_STOP_LOSS, stop_price
    if high >= tp2_price:
        return EXIT_TP2, tp2_price
    if high >= tp1_price:
        return EXIT_TP1, tp1_price
    return "", close  # no level hit; caller uses close for time/signal exit


def _intrabar_exit_short(
    low: float,
    high: float,
    close: float,
    stop_price: float,
    tp1_price: float,
    tp2_price: float,
) -> Tuple[str, float]:
    """Return (exit_reason, exit_price) for a short position. Precedence: stop, TP2, TP1."""
    if high >= stop_price:
        return EXIT_STOP_LOSS, stop_price
    if low <= tp2_price:
        return EXIT_TP2, tp2_price
    if low <= tp1_price:
        return EXIT_TP1, tp1_price
    return "", close


def _run_protection_aware_simulation(
    config: Config,
    candles_by_symbol: Dict[str, List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, int]]:
    """
    Bar-by-bar simulation with protection-aware exits. Uses same entry logic as
    strategy_replay; for each open position checks bar OHLC for stop/TP/time/signal.
    Returns (trades, exit_reasons_per_exit, exit_reason_counts).
    """
    if not candles_by_symbol:
        return [], [], {}

    stop_tp = getattr(config, "stop_tp", None)
    if not stop_tp:
        from src.config.config import StopTPConfig
        stop_tp = StopTPConfig()
    atr_mult_sl = float(getattr(stop_tp, "atr_multiplier_sl", 1.5))
    tp1_r = float(getattr(stop_tp, "tp1_r_multiple", 1.0))
    tp2_r = float(getattr(stop_tp, "tp2_r_multiple", 2.0))
    max_hold_seconds = int(getattr(stop_tp, "max_hold_seconds", 3600) or 0) or 3600

    bar_interval_ms = _bar_interval_ms(candles_by_symbol)
    max_hold_bars = max(1, (max_hold_seconds * 1000) // bar_interval_ms)

    scorer = FlowImpulseScorer(config.score_weights, config.entry)
    symbols = sorted(candles_by_symbol.keys())
    ts_points = sorted({c["start_ts"] for lst in candles_by_symbol.values() for c in lst})
    ts_index_by_symbol: Dict[str, int] = {s: 0 for s in symbols}

    trades: List[Dict[str, Any]] = []
    exit_reasons: List[str] = []
    open_positions: Dict[str, _OpenPosition] = {}
    trade_idx = 0

    for ts in ts_points:
        features_list: List[SymbolFeatures] = []
        symbol_for_feat: List[str] = []
        for symbol in symbols:
            candles = candles_by_symbol[symbol]
            idx = ts_index_by_symbol[symbol]
            while idx < len(candles) and candles[idx]["start_ts"] < ts:
                idx += 1
            if idx >= len(candles) or candles[idx]["start_ts"] != ts:
                ts_index_by_symbol[symbol] = idx
                continue
            ts_index_by_symbol[symbol] = idx
            f = _build_symbol_features_from_candles(symbol, candles, idx)
            features_list.append(f)
            symbol_for_feat.append(symbol)

        if not features_list:
            continue

        real_symbols = [s for s in symbol_for_feat if s != "_shadow"]
        if len(features_list) == 1:
            f0 = features_list[0]
            shadow = SymbolFeatures(
                symbol="_shadow",
                delta_30s=-f0.delta_30s,
                delta_1m=-f0.delta_1m,
                delta_3m=-f0.delta_3m,
                cvd_1m=-f0.cvd_1m,
                cvd_3m=-f0.cvd_3m,
                cvd_slope=-f0.cvd_slope,
                buy_sell_ratio_30s=2.0 - f0.buy_sell_ratio_30s if f0.buy_sell_ratio_30s else 1.0,
                buy_sell_ratio_1m=2.0 - f0.buy_sell_ratio_1m if f0.buy_sell_ratio_1m else 1.0,
                buy_sell_ratio_3m=2.0 - f0.buy_sell_ratio_3m if f0.buy_sell_ratio_3m else 1.0,
                price_return_1m=-f0.price_return_1m,
                price_return_3m=-f0.price_return_3m,
                price_return_5m=-f0.price_return_5m,
                distance_from_vwap=-f0.distance_from_vwap,
                atr_14=f0.atr_14,
                spread_bps=f0.spread_bps,
                realized_volatility=f0.realized_volatility,
                open_interest_change=-f0.open_interest_change,
                funding_rate=f0.funding_rate,
                long_short_ratio=f0.long_short_ratio,
                trade_count_1m=f0.trade_count_1m,
                trade_count_3m=f0.trade_count_3m,
                last_price=f0.last_price,
                vwap=f0.vwap,
            )
            features_list = [f0, shadow]
            symbol_for_feat = [real_symbols[0], "_shadow"]

        signals = scorer.score_all(features_list, stage4_enabled=False)
        signal_by_symbol: Dict[str, str] = {s.symbol: s.direction for s in signals}

        # Process exits only for symbols that have current bar (so OHLC is for this ts)
        to_close: List[str] = []
        for symbol in real_symbols:
            if symbol not in open_positions or symbol not in candles_by_symbol:
                continue
            pos = open_positions[symbol]
            candles = candles_by_symbol[symbol]
            idx = ts_index_by_symbol.get(symbol, 0)
            if idx >= len(candles):
                continue
            c = candles[idx]
            o, h, l, close = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            pos.bars_held += 1

            atr = max(pos.atr_at_entry, 1e-9)
            stop_price, tp1_price, tp2_price = _protection_levels(
                pos.entry_price, atr, pos.side, atr_mult_sl, tp1_r, tp2_r
            )

            exit_reason = ""
            exit_price = close
            if pos.side == "Buy":
                reason, exit_price = _intrabar_exit_long(l, h, close, stop_price, tp1_price, tp2_price)
            else:
                reason, exit_price = _intrabar_exit_short(l, h, close, stop_price, tp1_price, tp2_price)

            if reason:
                exit_reason = reason
            elif (ts - pos.entry_ts) >= max_hold_seconds * 1000:
                exit_reason = EXIT_TIME_STOP
                exit_price = close
            else:
                direction = signal_by_symbol.get(symbol, "none")
                if (pos.side == "Buy" and direction == "short") or (pos.side == "Sell" and direction == "long"):
                    exit_reason = EXIT_OPPOSITE_SIGNAL
                    exit_price = close
                elif pos.bars_held >= max_hold_bars:
                    exit_reason = EXIT_MAX_HOLD
                    exit_price = close

            if exit_reason:
                pnl = (exit_price - pos.entry_price) if pos.side == "Buy" else (pos.entry_price - exit_price)
                exit_side = "Sell" if pos.side == "Buy" else "Buy"
                link_id = f"exit_{exit_reason}"
                trades.append({
                    "ts": pos.entry_ts,
                    "symbol": symbol,
                    "side": pos.side,
                    "qty": 1.0,
                    "price": pos.entry_price,
                    "order_id": f"warm_start_ent_{symbol}_{trade_idx}",
                    "order_link_id": "entry",
                    "pnl": None,
                })
                trades.append({
                    "ts": ts,
                    "symbol": symbol,
                    "side": exit_side,
                    "qty": 1.0,
                    "price": exit_price,
                    "order_id": f"warm_start_{link_id}_{symbol}_{trade_idx}",
                    "order_link_id": link_id,
                    "pnl": pnl,
                    "exit_reason": exit_reason,
                })
                exit_reasons.append(exit_reason)
                trade_idx += 1
                to_close.append(symbol)

        for symbol in to_close:
            open_positions.pop(symbol, None)

        # Entries: no open position and strong signal
        for symbol in symbol_for_feat:
            if symbol == "_shadow":
                continue
            if symbol in open_positions:
                continue
            direction = signal_by_symbol.get(symbol, "none")
            if direction not in ("long", "short"):
                continue
            candles = candles_by_symbol[symbol]
            idx = ts_index_by_symbol[symbol]
            price = float(candles[idx]["close"])
            f = next((x for x in features_list if x.symbol == symbol), None)
            atr = float(f.atr_14) if f and getattr(f, "atr_14", None) is not None else 0.0
            if atr <= 0:
                atr = price * 0.01  # fallback
            side = "Buy" if direction == "long" else "Sell"
            open_positions[symbol] = _OpenPosition(
                symbol=symbol,
                side=side,
                entry_ts=ts,
                entry_price=price,
                atr_at_entry=atr,
                bars_held=0,
            )

    trades.sort(key=lambda t: (t["ts"], t["symbol"]))
    exit_reason_counts: Dict[str, int] = defaultdict(int)
    for r in exit_reasons:
        exit_reason_counts[r] += 1
    return trades, exit_reasons, dict(exit_reason_counts)


def _apply_costs_and_metrics(
    trades: List[Dict[str, Any]],
    fee_bps: float,
    slippage_bps: float,
    exit_reason_counts: Dict[str, int],
    exit_reasons: List[str],
    initial_equity: float = 10_000.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Apply fee/slippage to exit rows and compute core + protection metrics."""
    per_side_bps = float(fee_bps or 0.0) + float(slippage_bps or 0.0)
    total_fee = 0.0
    total_slippage = 0.0
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
        if symbol not in entry_stack or not entry_stack[symbol]:
            continue
        entry_price = entry_stack[symbol].pop(0)
        avg_price = (abs(entry_price) + abs(price)) / 2.0
        notional = avg_price * float(t.get("qty") or 1.0)
        cost_per_round_trip = per_side_bps / 10000.0 * 2.0 * notional
        fee_part = float(fee_bps or 0.0) / max(per_side_bps, 1e-9) * cost_per_round_trip if per_side_bps > 0 else 0.0
        slippage_part = float(slippage_bps or 0.0) / max(per_side_bps, 1e-9) * cost_per_round_trip if per_side_bps > 0 else 0.0
        total_fee += fee_part
        total_slippage += slippage_part
        t["pnl"] = float(t.get("pnl") or 0.0) - cost_per_round_trip

    paired = compute_realized_pnl_by_pairing(trades)
    metrics = compute_core_metrics(paired, initial_equity=initial_equity)
    metrics["fees_summary"] = total_fee
    metrics["slippage_summary"] = total_slippage

    n_exits = len(exit_reasons)
    stop_outs = exit_reason_counts.get(EXIT_STOP_LOSS, 0)
    tp1_hits = exit_reason_counts.get(EXIT_TP1, 0)
    tp2_hits = exit_reason_counts.get(EXIT_TP2, 0)
    metrics["stop_out_rate"] = (stop_outs / n_exits) if n_exits else 0.0
    metrics["tp1_hit_rate"] = (tp1_hits / n_exits) if n_exits else 0.0
    metrics["tp2_hit_rate"] = (tp2_hits / n_exits) if n_exits else 0.0
    metrics["exit_reason_counts"] = dict(exit_reason_counts)

    pnls = [float(t.get("pnl") or 0) for t in paired if t.get("pnl") is not None]
    max_consec = 0
    curr = 0
    for p in pnls:
        if p < 0:
            curr += 1
            max_consec = max(max_consec, curr)
        else:
            curr = 0
    metrics["max_consecutive_losses"] = max_consec

    return paired, metrics


def run_backtest_on_candles(
    config: Config,
    candles_by_symbol: Dict[str, List[Dict[str, Any]]],
    fee_bps: float,
    slippage_bps: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """
    Run protection-aware backtest on candles: same entry logic as replay,
    exits simulated via OHLC stop/TP/time/signal. Applies fee/slippage and
    returns (trades_with_costs, metrics, meta). Engine name: parameter_aware_protection_backtest.
    """
    trades, exit_reasons, exit_reason_counts = _run_protection_aware_simulation(config, candles_by_symbol)
    if not trades:
        metrics = compute_core_metrics([])
        metrics["stop_out_rate"] = 0.0
        metrics["tp1_hit_rate"] = 0.0
        metrics["tp2_hit_rate"] = 0.0
        metrics["exit_reason_counts"] = {}
        metrics["max_consecutive_losses"] = 0
        metrics["fees_summary"] = 0.0
        metrics["slippage_summary"] = 0.0
        meta = {
            "engine": "parameter_aware_protection_backtest",
            "symbols": list(sorted(candles_by_symbol.keys())),
            "trade_count": 0,
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
        }
        return trades, metrics, meta

    paired, metrics = _apply_costs_and_metrics(
        trades, fee_bps, slippage_bps, exit_reason_counts, exit_reasons
    )
    meta = {
        "engine": "parameter_aware_protection_backtest",
        "symbols": list(sorted(candles_by_symbol.keys())),
        "trade_count": int(metrics.get("trade_count") or 0),
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "exit_reason_counts": exit_reason_counts,
    }
    return paired, metrics, meta
