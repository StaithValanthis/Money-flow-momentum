"""Flow impulse scoring: cross-sectional long/short signal generation."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from src.config.config import ScoreWeights, EntryThresholds
from src.data.feature_builder import SymbolFeatures
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class SignalResult:
    """Scored signal for one symbol."""

    symbol: str
    score: float
    direction: str  # "long" | "short" | "none"
    delta_1m: float
    buy_sell_ratio_1m: float
    price_return_1m: float
    spread_bps: float
    divergence_bonus: float
    raw_features: Optional[SymbolFeatures] = None


def zscore(x: np.ndarray, val: float) -> float:
    """Z-score of val relative to array. Returns 0 if std=0."""
    if len(x) == 0 or np.std(x) == 0:
        return 0.0
    return float((val - np.mean(x)) / np.std(x))


class FlowImpulseScorer:
    """Score symbols and rank long/short candidates."""

    def __init__(self, weights: ScoreWeights, thresholds: EntryThresholds):
        self.weights = weights
        self.thresholds = thresholds

    def score_all(
        self,
        features_list: list[SymbolFeatures],
        max_longs: int = 5,
        max_shorts: int = 5,
    ) -> list[SignalResult]:
        """Score all symbols, apply thresholds, return ranked signals."""
        if not features_list:
            return []

        arr = np.array
        deltas = arr([f.delta_1m for f in features_list])
        cvd_slopes = arr([f.cvd_slope for f in features_list])
        ratios = arr([f.buy_sell_ratio_1m for f in features_list])
        returns = arr([f.price_return_1m for f in features_list])
        oi_changes = arr([f.open_interest_change for f in features_list])
        spreads = arr([f.spread_bps for f in features_list])
        fundings = arr([f.funding_rate for f in features_list])

        results: list[SignalResult] = []
        for i, f in enumerate(features_list):
            z_delta = zscore(deltas, f.delta_1m)
            z_cvd = zscore(cvd_slopes, f.cvd_slope)
            z_ratio = zscore(ratios, f.buy_sell_ratio_1m)
            z_return = zscore(returns, f.price_return_1m)
            z_oi = zscore(oi_changes, f.open_interest_change)
            z_spread = zscore(spreads, f.spread_bps)

            # Funding penalty: positive funding = cost for longs
            funding_penalty = max(0, f.funding_rate) * 100  # scale

            score = (
                self.weights.w1_delta_1m * z_delta
                + self.weights.w2_cvd_slope_3m * z_cvd
                + self.weights.w3_buy_sell_ratio_1m * z_ratio
                + self.weights.w4_price_return_1m * z_return
                + self.weights.w5_oi_change * z_oi
                - self.weights.w6_spread_penalty * z_spread
                - self.weights.w7_funding_penalty * funding_penalty
            )

            # Divergence bonus
            div_bonus = self._divergence_bonus(f)
            score += div_bonus

            direction = "none"
            # ATR extension: avoid entries too far from recent range
            atr_ok = f.atr_14 <= 0 or f.last_price <= 0
            if not atr_ok:
                max_move = self.thresholds.max_atr_extension * f.atr_14 / f.last_price
                atr_ok = abs(f.price_return_1m) <= max_move

            if score >= self.thresholds.long_threshold:
                if (
                    f.delta_1m >= self.thresholds.min_delta_1m
                    and f.buy_sell_ratio_1m >= self.thresholds.min_buy_sell_ratio_long
                    and f.spread_bps <= self.thresholds.max_spread_bps
                    and (f.distance_from_vwap >= -0.002 or f.price_return_1m > 0)
                    and f.open_interest_change >= -0.01
                    and atr_ok
                ):
                    direction = "long"
            elif score <= self.thresholds.short_threshold:
                if (
                    f.delta_1m <= -self.thresholds.min_delta_1m
                    and f.buy_sell_ratio_1m <= self.thresholds.max_buy_sell_ratio_short
                    and f.spread_bps <= self.thresholds.max_spread_bps
                    and (f.distance_from_vwap <= 0.002 or f.price_return_1m < 0)
                    and f.open_interest_change <= 0.01
                    and atr_ok
                ):
                    direction = "short"

            results.append(
                SignalResult(
                    symbol=f.symbol,
                    score=score,
                    direction=direction,
                    delta_1m=f.delta_1m,
                    buy_sell_ratio_1m=f.buy_sell_ratio_1m,
                    price_return_1m=f.price_return_1m,
                    spread_bps=f.spread_bps,
                    divergence_bonus=div_bonus,
                    raw_features=f,
                )
            )

        # Sort by absolute score, cap by max_longs/max_shorts
        longs = sorted([r for r in results if r.direction == "long"], key=lambda x: -x.score)[:max_longs]
        shorts = sorted([r for r in results if r.direction == "short"], key=lambda x: x.score)[:max_shorts]

        return longs + shorts

    def _divergence_bonus(self, f: SymbolFeatures) -> float:
        """Bullish: price flat/down while CVD rises. Bearish: price flat/up while CVD falls."""
        bonus = 0.0
        # Bullish: return <= 0 but delta/cvd positive
        if f.price_return_1m <= 0.001 and f.delta_1m > 0 and f.cvd_slope > 0:
            bonus = self.thresholds.divergence_bonus
        # Bearish
        elif f.price_return_1m >= -0.001 and f.delta_1m < 0 and f.cvd_slope < 0:
            bonus = -self.thresholds.divergence_bonus
        return bonus
