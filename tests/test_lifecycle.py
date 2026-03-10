"""Tests for lifecycle manager."""

import pytest
from unittest.mock import MagicMock

from src.portfolio.lifecycle import LifecycleManager, LifecycleState, LifecyclePhase
from src.data.market_state import MarketStateManager
from src.config.config import Config


def test_breakeven_after_tp1():
    """After TP1 filled, should_move_to_breakeven returns True until marked."""
    config = Config()
    config.stop_tp.breakeven_after_tp1 = True
    market_state = MarketStateManager(config.features)
    mgr = LifecycleManager(config, market_state)
    state = LifecycleState(
        symbol="BTCUSDT",
        side="Buy",
        entry_price=50000,
        stop_loss=49000,
        take_profit=52000,
        atr_at_entry=500,
        size=0.1,
        entry_ts=1000000,
    )
    mgr.register(state)
    assert mgr.should_move_to_breakeven("BTCUSDT") is False  # still OPEN
    mgr.mark_tp1_filled("BTCUSDT", 1001000)
    assert mgr.should_move_to_breakeven("BTCUSDT") is True
    assert mgr.breakeven_price("BTCUSDT") == 50000
    mgr.mark_stop_at_breakeven("BTCUSDT")
    assert mgr.should_move_to_breakeven("BTCUSDT") is False


def test_time_stop():
    """should_time_stop True after max_hold_seconds."""
    config = Config()
    config.stop_tp.max_hold_seconds = 100
    market_state = MarketStateManager(config.features)
    mgr = LifecycleManager(config, market_state)
    state = LifecycleState(
        symbol="BTCUSDT",
        side="Buy",
        entry_price=50000,
        stop_loss=49000,
        take_profit=52000,
        atr_at_entry=500,
        size=0.1,
        entry_ts=1000000,
    )
    mgr.register(state)
    assert mgr.should_time_stop("BTCUSDT", 1000000 + 50 * 1000) is False
    assert mgr.should_time_stop("BTCUSDT", 1000000 + 101 * 1000) is True
