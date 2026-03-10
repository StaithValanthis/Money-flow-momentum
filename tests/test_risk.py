"""Tests for risk engine."""

import pytest
from src.risk.risk_engine import RiskEngine
from src.config.config import RiskConfig


def test_position_sizing():
    """Test position sizing from risk."""
    config = RiskConfig(risk_per_trade_pct=0.5, max_concurrent_positions=5)
    engine = RiskEngine(config, equity_usdt=10_000)

    result = engine.compute_position_size(
        symbol="BTCUSDT",
        side="Buy",
        entry_price=50_000,
        stop_price=49_000,
        qty_step=0.001,
        min_qty=0.001,
        min_notional=5,
        max_notional=100_000,
    )
    assert result.reject_reason is None
    assert result.qty > 0
    assert result.risk_usdt <= 55  # ~0.5% of 10k


def test_can_open_position():
    """Test position limit."""
    config = RiskConfig(max_concurrent_positions=2)
    engine = RiskEngine(config)
    ok, _ = engine.can_open_position(0, symbol="BTCUSDT")
    assert ok
    ok, _ = engine.can_open_position(2, symbol="BTCUSDT")
    assert not ok
