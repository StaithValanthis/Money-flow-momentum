"""Flow impulse scoring: cross-sectional long/short signal generation."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from src.config.config import ScoreWeights, EntryThresholds
from src.data.feature_builder import SymbolFeatures
from src.signals.regime_filters import RegimeLabel, regime_allows_entry
from src.signals.threshold_policy import ThresholdProfile, compute_adaptive_thresholds
from src.portfolio.correlation import cluster_by_correlation_proxy, cluster_blocked
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
    # Stage 4
    score_components: Optional[dict[str, float]] = None
    regime_label: Optional[str] = None
    threshold_profile: Optional[str] = None
    cluster_id: Optional[int] = None
    rejection_reason: Optional[str] = None


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
        stage4_enabled: bool = False,
        regime_labels: Optional[dict[str, RegimeLabel]] = None,
        threshold_profiles: Optional[dict[str, ThresholdProfile]] = None,
        symbol_to_cluster: Optional[dict[str, int]] = None,
        current_long_symbols: Optional[list[str]] = None,
        current_short_symbols: Optional[list[str]] = None,
    ) -> list[SignalResult]:
        """Score all symbols, apply thresholds, return ranked signals. Stage 4: regime, adaptive thresholds, cluster block, score components."""
        if not features_list:
            return []

        regime_labels = regime_labels or {}
        threshold_profiles = threshold_profiles or {}
        current_long_symbols = current_long_symbols or []
        current_short_symbols = current_short_symbols or []
        if stage4_enabled and not symbol_to_cluster and features_list:
            symbol_to_cluster = cluster_by_correlation_proxy(features_list, correlation_threshold=0.7)
        symbol_to_cluster = symbol_to_cluster or {}

        if stage4_enabled and self.thresholds.use_adaptive_thresholds and not threshold_profiles and features_list:
            threshold_profiles = compute_adaptive_thresholds(
                features_list,
                self.thresholds.long_threshold,
                self.thresholds.short_threshold,
                self.thresholds.max_spread_bps,
            )

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
            reg = regime_labels.get(f.symbol)
            z_delta = zscore(deltas, f.delta_1m)
            z_cvd = zscore(cvd_slopes, f.cvd_slope)
            z_ratio = zscore(ratios, f.buy_sell_ratio_1m)
            z_return = zscore(returns, f.price_return_1m)
            z_oi = zscore(oi_changes, f.open_interest_change)
            z_spread = zscore(spreads, f.spread_bps)

            funding_penalty = max(0, f.funding_rate) * 100

            base_score = (
                self.weights.w1_delta_1m * z_delta
                + self.weights.w2_cvd_slope_3m * z_cvd
                + self.weights.w3_buy_sell_ratio_1m * z_ratio
                + self.weights.w4_price_return_1m * z_return
                + self.weights.w5_oi_change * z_oi
                - self.weights.w6_spread_penalty * z_spread
                - self.weights.w7_funding_penalty * funding_penalty
            )
            div_bonus = self._divergence_bonus(f)
            score = base_score + div_bonus

            # Stage 4 components
            score_components = None
            regime_label_str = None
            threshold_profile_str = None
            cluster_id = symbol_to_cluster.get(f.symbol)
            rejection_reason = None

            if stage4_enabled:
                score_components = {
                    "base_flow": base_score,
                    "divergence_bonus": div_bonus,
                    "regime_mult": 0.0,
                    "spread_penalty": -self.weights.w6_spread_penalty * z_spread,
                    "persistence_bonus": 0.0,
                    "anti_chase_penalty": 0.0,
                    "exhaustion_penalty": 0.0,
                    "cluster_penalty": 0.0,
                }
                regime_label_str = None
                if reg:
                    regime_label_str = reg.combined
                persistence_bonus = 0.0
                if getattr(f, "cvd_persistence_score", 0) > 0.5:
                    persistence_bonus = self.thresholds.persistence_bonus
                score_components["persistence_bonus"] = persistence_bonus
                score += persistence_bonus
                anti_chase = 0.0
                if abs(getattr(f, "price_return_1m", 0)) > 0.01 and (f.delta_1m * (f.price_return_1m or 0)) > 0:
                    anti_chase = self.thresholds.anti_chase_penalty
                score_components["anti_chase_penalty"] = -anti_chase
                score -= anti_chase
                exhaustion_penalty = 0.0
                if getattr(f, "flow_exhaustion_score", 0) > 0.3:
                    exhaustion_penalty = 0.2
                score_components["exhaustion_penalty"] = -exhaustion_penalty
                score -= exhaustion_penalty

                prof = threshold_profiles.get(f.symbol)
                if prof:
                    threshold_profile_str = prof.profile_name
                else:
                    prof = None
            else:
                prof = None

            long_th = self.thresholds.long_threshold
            short_th = self.thresholds.short_threshold
            max_spread = self.thresholds.max_spread_bps
            if stage4_enabled and prof:
                long_th = prof.long_threshold
                short_th = prof.short_threshold
                max_spread = prof.max_spread_bps

            direction = "none"
            atr_ok = f.atr_14 <= 0 or f.last_price <= 0
            if not atr_ok:
                max_move = self.thresholds.max_atr_extension * f.atr_14 / f.last_price
                atr_ok = abs(f.price_return_1m) <= max_move

            if score >= long_th:
                if (
                    f.delta_1m >= self.thresholds.min_delta_1m
                    and f.buy_sell_ratio_1m >= self.thresholds.min_buy_sell_ratio_long
                    and f.spread_bps <= max_spread
                    and (f.distance_from_vwap >= -0.002 or (f.price_return_1m or 0) > 0)
                    and f.open_interest_change >= -0.01
                    and atr_ok
                ):
                    direction = "long"
                    if stage4_enabled and self.thresholds.use_regime_filter and reg and not regime_allows_entry(reg, self.thresholds.regime_block_trend, self.thresholds.regime_block_chop, "long"):
                        direction = "none"
                        rejection_reason = "regime_block"
                    elif stage4_enabled and cluster_blocked(f.symbol, symbol_to_cluster, current_long_symbols, current_short_symbols, self.thresholds.max_positions_per_cluster):
                        direction = "none"
                        rejection_reason = "cluster_block"
            elif score <= short_th:
                if (
                    f.delta_1m <= -self.thresholds.min_delta_1m
                    and f.buy_sell_ratio_1m <= self.thresholds.max_buy_sell_ratio_short
                    and f.spread_bps <= max_spread
                    and (f.distance_from_vwap <= 0.002 or (f.price_return_1m or 0) < 0)
                    and f.open_interest_change <= 0.01
                    and atr_ok
                ):
                    direction = "short"
                    if stage4_enabled and self.thresholds.use_regime_filter and reg and not regime_allows_entry(reg, self.thresholds.regime_block_trend, self.thresholds.regime_block_chop, "short"):
                        direction = "none"
                        rejection_reason = "regime_block"
                    elif stage4_enabled and cluster_blocked(f.symbol, symbol_to_cluster, current_long_symbols, current_short_symbols, self.thresholds.max_positions_per_cluster):
                        direction = "none"
                        rejection_reason = "cluster_block"

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
                    score_components=score_components,
                    regime_label=regime_label_str,
                    threshold_profile=threshold_profile_str,
                    cluster_id=cluster_id,
                    rejection_reason=rejection_reason,
                )
            )

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
