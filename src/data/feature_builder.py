"""Transform raw market state into features for scoring."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from src.config.config import FeatureConfig
from src.data.market_state import SymbolState
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class SymbolFeatures:
    """Computed features for one symbol."""

    symbol: str
    delta_30s: float
    delta_1m: float
    delta_3m: float
    cvd_1m: float
    cvd_3m: float
    cvd_slope: float
    buy_sell_ratio_30s: float
    buy_sell_ratio_1m: float
    buy_sell_ratio_3m: float
    price_return_1m: float
    price_return_3m: float
    price_return_5m: float
    distance_from_vwap: float
    atr_14: float
    spread_bps: float
    realized_volatility: float
    open_interest_change: float
    funding_rate: float
    long_short_ratio: float
    trade_count_1m: int
    trade_count_3m: int
    last_price: float
    vwap: float
    # Stage 4 flow features
    delta_acceleration: float = 0.0
    cvd_divergence_score: float = 0.0
    cvd_persistence_score: float = 0.0
    trade_intensity_burst: float = 0.0
    price_response_to_flow: float = 0.0
    flow_exhaustion_score: float = 0.0
    move_efficiency: float = 0.0
    volatility_expansion_ratio: float = 1.0
    breakout_confirmation_score: float = 0.0
    failed_breakout_score: float = 0.0


class FeatureBuilder:
    """Build features from market state."""

    def __init__(self, config: FeatureConfig):
        self.config = config

    def build(self, state: SymbolState) -> SymbolFeatures:
        """Build features from symbol state."""
        # CVD slope: approximate from delta over 3m window
        cvd_1m = state.delta_1m  # cumulative over 1m
        cvd_3m = state.delta_3m
        cvd_slope = state.delta_3m / (self.config.window_3m or 1) if self.config.window_3m else 0

        # Buy/sell ratios
        total_1m = state.buy_vol_1m + state.sell_vol_1m
        total_30s = state.buy_vol_30s + state.sell_vol_30s
        total_3m = state.buy_vol_3m + state.sell_vol_3m

        buy_sell_30s = state.buy_vol_30s / state.sell_vol_30s if state.sell_vol_30s > 0 else 1.0
        buy_sell_1m = state.buy_vol_1m / state.sell_vol_1m if state.sell_vol_1m > 0 else 1.0
        buy_sell_3m = state.buy_vol_3m / state.sell_vol_3m if state.sell_vol_3m > 0 else 1.0

        # Price returns from closes (assume 1m klines: 1 candle = 1 min)
        closes = list(state.closes)
        price_return_1m = 0.0
        price_return_3m = 0.0
        price_return_5m = 0.0
        if len(closes) >= 2:
            curr = closes[-1]
            if len(closes) >= 2 and closes[-2] > 0:
                price_return_1m = (curr - closes[-2]) / closes[-2]
            if len(closes) >= 4 and closes[-4] > 0:
                price_return_3m = (curr - closes[-4]) / closes[-4]
            if len(closes) >= 6 and closes[-6] > 0:
                price_return_5m = (curr - closes[-6]) / closes[-6]

        # Distance from VWAP
        vwap = state.vwap if state.vwap > 0 else state.last_price
        distance_from_vwap = (state.last_price - vwap) / vwap if vwap > 0 else 0

        # ATR
        atr = self._compute_atr(state)

        # Realized volatility (std of returns)
        vol = 0.0
        if len(closes) >= self.config.volatility_window:
            rets = np.diff(np.array(closes[-self.config.volatility_window :])) / np.array(closes[-self.config.volatility_window : -1])
            vol = float(np.std(rets)) if len(rets) > 0 else 0

        # Stage 4 flow features (robust to missing data)
        delta_accel = 0.0
        if state.delta_1m != 0 or state.delta_30s != 0:
            d_short = state.delta_30s * 2 if state.delta_30s != 0 else state.delta_1m
            delta_accel = state.delta_1m - d_short
            if abs(d_short) > 1e-9:
                delta_accel = delta_accel / abs(d_short)
        cvd_div = 0.0
        if cvd_slope != 0 and state.last_price > 0:
            ret_sign = 1 if price_return_1m > 0 else (-1 if price_return_1m < 0 else 0)
            cvd_sign = 1 if cvd_slope > 0 else (-1 if cvd_slope < 0 else 0)
            if ret_sign != 0 and ret_sign != cvd_sign:
                cvd_div = -0.5
            elif ret_sign == cvd_sign and ret_sign != 0:
                cvd_div = 0.3
        cvd_pers = 0.0
        if state.delta_3m != 0 and state.delta_1m != 0:
            cvd_pers = min(1.0, abs(state.delta_1m) / (abs(state.delta_3m) / 3 + 1e-9))
        intensity_burst = 0.0
        if state.trade_count_3m > 0:
            intensity_burst = (state.trade_count_1m * 3) / (state.trade_count_3m + 1) - 1.0
        price_response = 0.0
        if state.delta_1m != 0 and state.last_price > 0:
            price_response = price_return_1m * (1 if state.delta_1m > 0 else -1)
        exhaustion = 0.0
        if abs(state.delta_1m) > 1e-9 and state.last_price > 0:
            move_per_flow = abs(price_return_1m) / (abs(state.delta_1m) / (state.last_price * state.trade_count_1m) if state.trade_count_1m else 1e-9)
            if move_per_flow < 0.5 and abs(price_return_1m) < 0.01:
                exhaustion = 0.5
        move_eff = 0.0
        if abs(state.delta_1m) > 1e-9 and state.last_price > 0:
            total_vol_1m = state.buy_vol_1m + state.sell_vol_1m
            if total_vol_1m > 0:
                move_eff = price_return_1m / (state.delta_1m / total_vol_1m + 1e-9)
        vol_expansion = 1.0
        if len(closes) >= self.config.volatility_window * 2:
            recent = np.diff(np.array(closes[-self.config.volatility_window:])) / (np.array(closes[-self.config.volatility_window:-1]) + 1e-9)
            older = np.diff(np.array(closes[-self.config.volatility_window*2:-self.config.volatility_window])) / (np.array(closes[-self.config.volatility_window*2:-self.config.volatility_window-1]) + 1e-9)
            std_r = float(np.std(recent)) if len(recent) > 0 else 0
            std_o = float(np.std(older)) if len(older) > 0 else 0
            if std_o > 1e-12:
                vol_expansion = std_r / std_o
        breakout_conf = 0.0
        if price_return_1m * cvd_slope > 0 and abs(price_return_1m) > 0.002:
            breakout_conf = 0.3
        failed_breakout = 0.0
        if price_return_1m * cvd_slope < 0 and abs(price_return_1m) > 0.003:
            failed_breakout = 0.5

        return SymbolFeatures(
            symbol=state.symbol,
            delta_30s=state.delta_30s,
            delta_1m=state.delta_1m,
            delta_3m=state.delta_3m,
            cvd_1m=cvd_1m,
            cvd_3m=cvd_3m,
            cvd_slope=cvd_slope,
            buy_sell_ratio_30s=buy_sell_30s,
            buy_sell_ratio_1m=buy_sell_1m,
            buy_sell_ratio_3m=buy_sell_3m,
            price_return_1m=price_return_1m,
            price_return_3m=price_return_3m,
            price_return_5m=price_return_5m,
            distance_from_vwap=distance_from_vwap,
            atr_14=atr,
            spread_bps=state.spread_bps,
            realized_volatility=vol,
            open_interest_change=state.oi_change,
            funding_rate=state.funding_rate,
            long_short_ratio=state.long_short_ratio,
            trade_count_1m=state.trade_count_1m,
            trade_count_3m=state.trade_count_3m,
            last_price=state.last_price,
            vwap=vwap,
            delta_acceleration=delta_accel,
            cvd_divergence_score=cvd_div,
            cvd_persistence_score=cvd_pers,
            trade_intensity_burst=intensity_burst,
            price_response_to_flow=price_response,
            flow_exhaustion_score=exhaustion,
            move_efficiency=move_eff,
            volatility_expansion_ratio=vol_expansion,
            breakout_confirmation_score=breakout_conf,
            failed_breakout_score=failed_breakout,
        )

    def _compute_atr(self, state: SymbolState) -> float:
        """Compute ATR(14) from highs/lows/closes."""
        n = self.config.atr_period
        highs = list(state.highs)
        lows = list(state.lows)
        closes = list(state.closes)
        if len(closes) < n + 1:
            return 0.0
        tr_list = []
        for i in range(-n - 1, 0):
            h = highs[i] if i < len(highs) else closes[i]
            l = lows[i] if i < len(lows) else closes[i]
            prev_c = closes[i - 1] if i > -len(closes) else closes[i]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)
        return float(np.mean(tr_list)) if tr_list else 0.0
