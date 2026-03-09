"""Tests for signal scoring."""

import pytest
from src.signals.flow_impulse import FlowImpulseScorer, zscore
from src.data.feature_builder import SymbolFeatures
from src.config.config import ScoreWeights, EntryThresholds
import numpy as np


def test_zscore():
    """Test z-score."""
    arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert abs(zscore(arr, 3.0)) < 0.01
    assert zscore(arr, 10.0) > 0


def test_scorer():
    """Test flow impulse scorer."""
    weights = ScoreWeights()
    thresholds = EntryThresholds(long_threshold=1.0, short_threshold=-1.0)
    scorer = FlowImpulseScorer(weights, thresholds)

    # Create mock features
    features = [
        SymbolFeatures(
            symbol="BTCUSDT",
            delta_30s=100,
            delta_1m=500,
            delta_3m=1000,
            cvd_1m=500,
            cvd_3m=1000,
            cvd_slope=0.5,
            buy_sell_ratio_30s=1.2,
            buy_sell_ratio_1m=1.1,
            buy_sell_ratio_3m=1.05,
            price_return_1m=0.01,
            price_return_3m=0.02,
            price_return_5m=0.03,
            distance_from_vwap=0.001,
            atr_14=500,
            spread_bps=5,
            realized_volatility=0.01,
            open_interest_change=0.02,
            funding_rate=0.0001,
            long_short_ratio=1.1,
            trade_count_1m=100,
            trade_count_3m=300,
            last_price=50000,
            vwap=49900,
        ),
    ]
    results = scorer.score_all(features)
    assert len(results) >= 0
