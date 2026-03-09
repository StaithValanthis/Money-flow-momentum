"""Tests for universe filtering."""

import pytest
from unittest.mock import MagicMock

from src.data.universe import UniverseManager
from src.config.config import UniverseConfig


def test_universe_filter_allowlist():
    """Test allowlist filter."""
    config = UniverseConfig(allowlist=["BTCUSDT", "ETHUSDT"], min_24h_turnover_usdt=0)
    client = MagicMock()
    client.get_all_linear_instruments.return_value = [
        {"symbol": "BTCUSDT", "status": "Trading", "quoteCoin": "USDT", "lotSizeFilter": {"minNotionalValue": "5"}},
        {"symbol": "ETHUSDT", "status": "Trading", "quoteCoin": "USDT", "lotSizeFilter": {"minNotionalValue": "5"}},
        {"symbol": "SOLUSDT", "status": "Trading", "quoteCoin": "USDT", "lotSizeFilter": {"minNotionalValue": "5"}},
    ]
    client.get_tickers.return_value = {
        "result": {"list": [
            {"symbol": "BTCUSDT", "turnover24h": "1e9", "bid1Price": "50000", "ask1Price": "50001", "lastPrice": "50000"},
            {"symbol": "ETHUSDT", "turnover24h": "1e9", "bid1Price": "3000", "ask1Price": "3001", "lastPrice": "3000"},
            {"symbol": "SOLUSDT", "turnover24h": "1e9", "bid1Price": "100", "ask1Price": "101", "lastPrice": "100"},
        ]}
    }
    mgr = UniverseManager(client, config)
    symbols = mgr.refresh()
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols
    assert "SOLUSDT" not in symbols
