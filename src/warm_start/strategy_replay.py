"""Strategy replay on candles for warm-start.

This module approximates the real strategy behaviour using FlowImpulseScorer
and EntryThresholds over historical candles. It does NOT run the full live
runtime, but it reuses the actual scoring and entry logic as closely as is
practical for warm-start calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from src.config.config import Config
from src.data.feature_builder import SymbolFeatures
from src.signals.flow_impulse import FlowImpulseScorer


@dataclass
class _OpenPosition:
    symbol: str
    side: str  # "Buy" | "Sell"
    entry_ts: int
    entry_price: float
    bars_held: int = 0


def _build_symbol_features_from_candles(
    symbol: str,
    candles: List[Dict[str, Any]],
    idx: int,
) -> SymbolFeatures:
    """
    Build a minimal SymbolFeatures snapshot from candles only.

    This is an approximation: flow/volume/oi/funding/LSR are dummy or zeroed,
    but price returns and ATR-like context are captured where possible.
    """
    # Defensively slice candles up to idx (inclusive)
    window = candles[: idx + 1]
    last = window[-1]
    closes = [c["close"] for c in window]

    # Price returns
    price_return_1m = 0.0
    price_return_3m = 0.0
    price_return_5m = 0.0
    if len(closes) >= 2 and closes[-2] > 0:
        price_return_1m = (closes[-1] - closes[-2]) / closes[-2]
    if len(closes) >= 4 and closes[-4] > 0:
        price_return_3m = (closes[-1] - closes[-4]) / closes[-4]
    if len(closes) >= 6 and closes[-6] > 0:
        price_return_5m = (closes[-1] - closes[-6]) / closes[-6]

    # Very rough ATR-style proxy from highs/lows
    highs = [c["high"] for c in window]
    lows = [c["low"] for c in window]
    atr_14 = 0.0
    n = min(14, len(window))
    if n >= 2:
        tr_list = []
        # Use forward indices to avoid negative index edge cases.
        for i in range(1, n):
            h = highs[i]
            l = lows[i]
            prev_c = closes[i - 1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)
        if tr_list:
            atr_14 = sum(tr_list) / len(tr_list)

    last_price = last["close"]
    vwap = last_price  # no volume breakdown; use close as proxy

    # Without trade-level flow we approximate these as neutral / zero.
    return SymbolFeatures(
        symbol=symbol,
        delta_30s=0.0,
        delta_1m=price_return_1m * 100.0,  # scale so sign/magnitude matter
        delta_3m=price_return_3m * 100.0,
        cvd_1m=0.0,
        cvd_3m=0.0,
        cvd_slope=price_return_3m,  # directional proxy
        buy_sell_ratio_30s=1.0,
        buy_sell_ratio_1m=1.0,
        buy_sell_ratio_3m=1.0,
        price_return_1m=price_return_1m,
        price_return_3m=price_return_3m,
        price_return_5m=price_return_5m,
        distance_from_vwap=0.0,
        atr_14=atr_14,
        spread_bps=10.0,
        realized_volatility=0.0,
        open_interest_change=0.0,
        funding_rate=0.0,
        long_short_ratio=1.0,
        trade_count_1m=1,
        trade_count_3m=min(len(window), 3),
        last_price=last_price,
        vwap=vwap,
    )


def replay_strategy_from_candles(
    config: Config,
    candles_by_symbol: Dict[str, List[Dict[str, Any]]],
    max_hold_bars: int = 5,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Approximate the real strategy on candles:
    - builds SymbolFeatures from candles,
    - runs FlowImpulseScorer with config.score_weights and config.entry,
    - opens/closes one position per symbol based on signals,
    - emits entry+exit trade rows compatible with evaluation/optimizer.

    Returns (trades, meta).
    """
    if not candles_by_symbol:
        return [], {"engine": "strategy_replay", "symbols": [], "max_hold_bars": max_hold_bars}

    scorer = FlowImpulseScorer(config.score_weights, config.entry)

    # Align on per-symbol candle indices; we assume candles are sorted by start_ts.
    symbols = sorted(candles_by_symbol.keys())
    # Determine a global time-ordered index of bars by repeatedly stepping each symbol
    # up to its own length; at each step we evaluate all symbols that have data.
    # For simplicity we iterate per-symbol independently and then merge by ts.
    # This still exercises the scoring logic per symbol with config parameters.

    trades: List[Dict[str, Any]] = []
    open_positions: Dict[str, _OpenPosition] = {}

    # Build a unified sorted list of all timestamps
    ts_points = sorted(
        {c["start_ts"] for lst in candles_by_symbol.values() for c in lst}
    )
    ts_index_by_symbol: Dict[str, int] = {s: 0 for s in symbols}

    for ts in ts_points:
        features_list: List[SymbolFeatures] = []
        symbol_for_feat: List[str] = []
        for symbol in symbols:
            candles = candles_by_symbol[symbol]
            idx = ts_index_by_symbol[symbol]
            # Advance index until candle.start_ts >= ts
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
            # No symbols have this timestamp
            continue

        # Score using the real FlowImpulseScorer logic (Stage 4 disabled for simplicity).
        signals = scorer.score_all(features_list, stage4_enabled=False)
        signal_by_symbol: Dict[str, str] = {s.symbol: s.direction for s in signals}

        # Update positions for each symbol
        for symbol in symbol_for_feat:
            direction = signal_by_symbol.get(symbol, "none")
            candles = candles_by_symbol[symbol]
            idx = ts_index_by_symbol[symbol]
            price = candles[idx]["close"]

            pos = open_positions.get(symbol)

            # Exit logic: opposite signal or max_hold_bars
            should_exit = False
            if pos:
                pos.bars_held += 1
                if direction == "none":
                    # No fresh signal; exit on max_hold_bars
                    should_exit = pos.bars_held >= max_hold_bars
                elif (pos.side == "Buy" and direction == "short") or (pos.side == "Sell" and direction == "long"):
                    should_exit = True

                if should_exit:
                    exit_side = "Sell" if pos.side == "Buy" else "Buy"
                    pnl = (price - pos.entry_price) if pos.side == "Buy" else (pos.entry_price - price)
                    trade_idx = len(trades) // 2
                    trades.append(
                        {
                            "ts": pos.entry_ts,
                            "symbol": symbol,
                            "side": pos.side,
                            "qty": 1.0,
                            "price": pos.entry_price,
                            "order_id": f"warm_start_ent_{symbol}_{trade_idx}",
                            "order_link_id": "entry",
                            "pnl": None,
                        }
                    )
                    trades.append(
                        {
                            "ts": ts,
                            "symbol": symbol,
                            "side": exit_side,
                            "qty": 1.0,
                            "price": price,
                            "order_id": f"warm_start_tp1_{symbol}_{trade_idx}",
                            "order_link_id": "tp1_1",
                            "pnl": pnl,
                        }
                    )
                    del open_positions[symbol]
                    pos = None

            # Entry logic: no open position and strong signal
            if not pos and direction in ("long", "short"):
                side = "Buy" if direction == "long" else "Sell"
                open_positions[symbol] = _OpenPosition(
                    symbol=symbol,
                    side=side,
                    entry_ts=ts,
                    entry_price=price,
                    bars_held=0,
                )

    trades.sort(key=lambda t: (t["ts"], t["symbol"]))
    meta: Dict[str, Any] = {
        "engine": "strategy_replay",
        "symbols": symbols,
        "max_hold_bars": max_hold_bars,
        "trade_count": len([t for t in trades if t.get("pnl") is not None]),
    }
    return trades, meta

