"""Tests for feature calculations."""

import pytest
from src.data.market_state import MarketStateManager, SymbolState
from src.data.feature_builder import FeatureBuilder
from src.config.config import FeatureConfig


def test_delta_aggregation():
    """Test buy/sell volume and delta aggregation."""
    config = FeatureConfig()
    mgr = MarketStateManager(config)
    state = mgr.ensure_symbol("BTCUSDT")
    state.max_ts_ms = 100_000

    # Simulate trades
    mgr.on_trade({"s": "BTCUSDT", "T": 99_000, "S": "Buy", "v": "0.1", "p": "50000"})
    mgr.on_trade({"s": "BTCUSDT", "T": 99_500, "S": "Sell", "v": "0.05", "p": "50000"})

    assert state.delta_1m > 0  # More buy than sell
    assert state.buy_vol_1m > state.sell_vol_1m


def test_feature_builder():
    """Test feature builder produces valid features."""
    config = FeatureConfig()
    builder = FeatureBuilder(config)
    state = SymbolState(symbol="BTCUSDT")
    state.delta_1m = 1000
    state.delta_3m = 2000
    state.buy_vol_1m = 1500
    state.sell_vol_1m = 500
    state.buy_vol_30s = 400
    state.sell_vol_30s = 100
    state.buy_vol_3m = 2500
    state.sell_vol_3m = 500
    state.last_price = 50000
    state.vwap = 49900
    state.spread_bps = 5
    state.closes.extend([49000, 49500, 50000])
    state.highs.extend([49500, 50000, 50100])
    state.lows.extend([48500, 49000, 49900])

    f = builder.build(state)
    assert f.symbol == "BTCUSDT"
    assert f.delta_1m == 1000
    assert f.buy_sell_ratio_1m > 1
    assert f.spread_bps == 5
