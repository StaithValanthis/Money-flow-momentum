"""Flow-impulse strategy: wraps FeatureBuilder + FlowImpulseScorer for strategy interface."""

from typing import Any, Optional

from src.config.config import Config
from src.data.feature_builder import FeatureBuilder, SymbolFeatures
from src.data.market_state import SymbolState
from src.signals.flow_impulse import FlowImpulseScorer, SignalResult
from src.signals.regime_filters import classify_regime
from src.signals.threshold_policy import compute_adaptive_thresholds
from src.portfolio.correlation import cluster_by_correlation_proxy
from src.strategies.base import BaseStrategy, ScoredCandidate
from src.utils.logging import get_logger

log = get_logger(__name__)


class FlowImpulseStrategy(BaseStrategy):
    """Flow-impulse strategy: cross-sectional flow scoring, Stage 4 regime/threshold/cluster."""

    def __init__(self, config: Config):
        self.config = config
        self._feature_builder = FeatureBuilder(config.features)
        self._scorer = FlowImpulseScorer(config.score_weights, config.entry)

    @property
    def name(self) -> str:
        return "flow_impulse"

    def build_features(self, state: SymbolState) -> SymbolFeatures:
        return self._feature_builder.build(state)

    def score_candidates(
        self,
        features_list: list[SymbolFeatures],
        max_longs: int = 5,
        max_shorts: int = 5,
        **kwargs: Any,
    ) -> list[ScoredCandidate]:
        stage4 = getattr(self.config, "stage4_enabled", False)
        regime_labels = kwargs.get("regime_labels") or {}
        threshold_profiles = kwargs.get("threshold_profiles") or {}
        symbol_to_cluster = kwargs.get("symbol_to_cluster") or {}
        current_long_symbols = kwargs.get("current_long_symbols") or []
        current_short_symbols = kwargs.get("current_short_symbols") or []
        if stage4 and not symbol_to_cluster and features_list:
            symbol_to_cluster = cluster_by_correlation_proxy(features_list, correlation_threshold=0.7)
        if stage4 and self.config.entry.use_adaptive_thresholds and not threshold_profiles and features_list:
            threshold_profiles = compute_adaptive_thresholds(
                features_list,
                self.config.entry.long_threshold,
                self.config.entry.short_threshold,
                self.config.entry.max_spread_bps,
            )
        raw = self._scorer.score_all(
            features_list,
            max_longs=max_longs,
            max_shorts=max_shorts,
            stage4_enabled=stage4,
            regime_labels=regime_labels,
            threshold_profiles=threshold_profiles,
            symbol_to_cluster=symbol_to_cluster,
            current_long_symbols=current_long_symbols,
            current_short_symbols=current_short_symbols,
        )
        out: list[ScoredCandidate] = []
        for r in raw:
            if r.direction in ("long", "short"):
                out.append(
                    ScoredCandidate(
                        symbol=r.symbol,
                        direction=r.direction,
                        score=r.score,
                        raw_features=r.raw_features,
                        meta={
                            "delta_1m": r.delta_1m,
                            "buy_sell_ratio_1m": r.buy_sell_ratio_1m,
                            "price_return_1m": r.price_return_1m,
                            "spread_bps": r.spread_bps,
                            "regime_label": r.regime_label,
                            "threshold_profile": r.threshold_profile,
                            "cluster_id": r.cluster_id,
                            "rejection_reason": r.rejection_reason,
                        },
                    )
                )
        return out
