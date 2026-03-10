"""Tests for context refresher and eligibility."""

import time
from unittest.mock import MagicMock

import pytest

from src.data.context_refresher import ContextRefresher, ContextStaleness
from src.data.eligibility import check_eligibility
from src.data.market_state import MarketStateManager
from src.data.universe import UniverseManager
from src.portfolio.position_manager import PositionManager
from src.config.config import Config, FeatureConfig, UniverseConfig


def test_context_staleness_gating():
    """Symbol with stale context should be ineligible."""
    config = Config()
    config.context_staleness_seconds = 60
    client = MagicMock()
    universe = UniverseManager(client, UniverseConfig())
    universe._symbols = ["BTCUSDT"]
    universe._instruments = {"BTCUSDT": {}}
    market_state = MarketStateManager(FeatureConfig())
    context = ContextRefresher(client, config, market_state, universe)
    now_ms = int(time.time() * 1000)
    # No refresh yet: klines missing
    fresh, reason = context.is_symbol_context_fresh("BTCUSDT", now_ms)
    assert fresh is False
    assert "stale" in reason or "klines" in reason


def test_eligibility_not_in_universe():
    """Symbol not in universe is ineligible."""
    client = MagicMock()
    universe = UniverseManager(client, UniverseConfig())
    universe._symbols = []
    from src.data.context_refresher import ContextRefresher
    from src.data.market_state import MarketStateManager
    config = Config()
    market_state = MarketStateManager(FeatureConfig())
    context = ContextRefresher(client, config, market_state, universe)
    positions = PositionManager(config.risk)
    ok, reason = check_eligibility("BTCUSDT", universe, context, positions, int(time.time() * 1000))
    assert ok is False
    assert "not_in_universe" in reason or "universe" in reason.lower()
