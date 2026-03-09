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
