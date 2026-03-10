"""Regime classification: trend vs chop, vol expansion vs compression, momentum vs mean-reversion."""

from dataclasses import dataclass
from typing import Optional

from src.data.feature_builder import SymbolFeatures
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class RegimeLabel:
    """Per-symbol regime classification."""

    symbol: str
    trend_vs_chop: str  # "trend" | "chop" | "unknown"
    vol_regime: str  # "low_vol" | "high_vol" | "expansion" | "compression" | "unknown"
    momentum_vs_mean_revert: str  # "momentum" | "mean_revert" | "unknown"
    combined: str  # short label for logging


def classify_regime(f: SymbolFeatures, atr_percentile_50: float = 0.0) -> RegimeLabel:
    """
    Classify regime from features. Uses ATR and realized vol for vol regime;
    price persistence and flow-price alignment for trend vs chop;
    breakout confirmation vs failed breakout for momentum vs mean-revert.
    """
    trend = "unknown"
    vol_reg = "unknown"
    mom = "unknown"

    if f.atr_14 and f.last_price:
        atr_pct = f.atr_14 / f.last_price
        if atr_percentile_50 and atr_pct > atr_percentile_50 * 1.2:
            vol_reg = "high_vol"
        elif atr_percentile_50 and atr_pct < atr_percentile_50 * 0.8:
            vol_reg = "low_vol"
        elif f.volatility_expansion_ratio > 1.2:
            vol_reg = "expansion"
        elif f.volatility_expansion_ratio < 0.8 and f.volatility_expansion_ratio > 0:
            vol_reg = "compression"

    if f.price_response_to_flow > 0.001 and abs(f.price_return_1m) > 0.002:
        trend = "trend"
    elif f.cvd_divergence_score < 0 or (abs(f.price_return_1m) < 0.001 and f.cvd_slope != 0):
        trend = "chop"

    if f.breakout_confirmation_score > 0.2:
        mom = "momentum"
    elif f.failed_breakout_score > 0.2:
        mom = "mean_revert"

    combined = f"{trend}_{vol_reg}_{mom}"
    return RegimeLabel(
        symbol=f.symbol,
        trend_vs_chop=trend,
        vol_regime=vol_reg,
        momentum_vs_mean_revert=mom,
        combined=combined,
    )


def regime_allows_entry(
    label: RegimeLabel,
    block_trend: bool,
    block_chop: bool,
    direction: str,
) -> bool:
    """True if regime does not block entry. block_trend=True means block entries when regime is trend (conservative)."""
    if block_chop and label.trend_vs_chop == "chop":
        return False
    if block_trend and label.trend_vs_chop == "trend":
        return False
    return True
