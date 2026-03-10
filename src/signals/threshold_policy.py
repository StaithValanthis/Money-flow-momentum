"""Adaptive thresholds by liquidity and volatility bucket."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from src.data.feature_builder import SymbolFeatures
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ThresholdProfile:
    """Applied threshold profile for a symbol."""

    profile_name: str
    long_threshold: float
    short_threshold: float
    max_spread_bps: float
    liquidity_bucket: str
    volatility_bucket: str


def _percentile_bucket(value: float, p33: float, p66: float, low: str, mid: str, high: str) -> str:
    if p33 == p66:
        return mid
    if value <= p33:
        return low
    if value >= p66:
        return high
    return mid


def compute_adaptive_thresholds(
    features_list: list[SymbolFeatures],
    base_long: float,
    base_short: float,
    base_max_spread_bps: float,
    illiquid_spread_penalty_bps: float = 5.0,
    high_vol_threshold_add: float = 0.2,
) -> dict[str, ThresholdProfile]:
    """
    Bucket symbols by liquidity (trade_count * notional proxy) and volatility (ATR).
    Return per-symbol profile: illiquid/low_vol get stricter spread; high_vol get higher score gate.
    """
    if not features_list:
        return {}
    arr = np.array
    trade_notional = arr([f.trade_count_1m * (f.last_price or 1) for f in features_list])
    atrs = arr([f.atr_14 / (f.last_price or 1) * 100 for f in features_list if f.last_price])
    if len(atrs) < len(features_list):
        atrs = np.resize(atrs, len(features_list))
    p33_tn = float(np.percentile(trade_notional, 33)) if len(trade_notional) else 0
    p66_tn = float(np.percentile(trade_notional, 66)) if len(trade_notional) else 0
    p33_atr = float(np.percentile(atrs, 33)) if len(atrs) else 0
    p66_atr = float(np.percentile(atrs, 66)) if len(atrs) else 0

    out = {}
    for f in features_list:
        tn = f.trade_count_1m * (f.last_price or 1)
        atr_pct = (f.atr_14 / (f.last_price or 1) * 100) if f.last_price else 0
        liq_bucket = _percentile_bucket(tn, p33_tn, p66_tn, "low", "mid", "high")
        vol_bucket = _percentile_bucket(atr_pct, p33_atr, p66_atr, "low", "mid", "high")

        long_t = base_long
        short_t = base_short
        spread_bps = base_max_spread_bps
        if liq_bucket == "low":
            spread_bps = max(5, base_max_spread_bps - illiquid_spread_penalty_bps)
        if vol_bucket == "high":
            long_t = base_long + high_vol_threshold_add
            short_t = base_short - high_vol_threshold_add
        profile_name = f"{liq_bucket}_{vol_bucket}"
        out[f.symbol] = ThresholdProfile(
            profile_name=profile_name,
            long_threshold=long_t,
            short_threshold=short_t,
            max_spread_bps=spread_bps,
            liquidity_bucket=liq_bucket,
            volatility_bucket=vol_bucket,
        )
    return out
