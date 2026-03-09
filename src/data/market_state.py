"""In-memory rolling market state per symbol for flow metrics."""

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from src.config.config import FeatureConfig
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class TradeRecord:
    """Single trade from public stream."""

    ts: int  # ms
    symbol: str
    side: str  # Buy, Sell
    size: float
    price: float
    tick_direction: str = ""


@dataclass
class SymbolState:
    """Rolling state for one symbol."""

    symbol: str
    max_ts_ms: int = 0
    # Trade buffers (ts, side, size, price)
    trades_5m: deque = field(default_factory=lambda: deque(maxlen=5000))
    # Aggregates
    buy_vol_30s: float = 0.0
    sell_vol_30s: float = 0.0
    buy_vol_1m: float = 0.0
    sell_vol_1m: float = 0.0
    buy_vol_3m: float = 0.0
    sell_vol_3m: float = 0.0
    delta_30s: float = 0.0
    delta_1m: float = 0.0
    delta_3m: float = 0.0
    cvd: float = 0.0
    vwap: float = 0.0
    vwap_vol: float = 0.0
    trade_count_30s: int = 0
    trade_count_1m: int = 0
    trade_count_3m: int = 0
    last_price: float = 0.0
    spread_bps: float = 0.0
    # External context (updated by REST)
    funding_rate: float = 0.0
    open_interest: float = 0.0
    oi_change: float = 0.0
    long_short_ratio: float = 1.0
    # Kline data for ATR/returns
    closes: deque = field(default_factory=lambda: deque(maxlen=100))
    highs: deque = field(default_factory=lambda: deque(maxlen=100))
    lows: deque = field(default_factory=lambda: deque(maxlen=100))


class MarketStateManager:
    """Manage rolling state for all symbols."""

    def __init__(self, config: FeatureConfig):
        self.config = config
        self._states: dict[str, SymbolState] = {}
        self._cutoff_5m_ms = config.window_5m * 1000

    def ensure_symbol(self, symbol: str) -> SymbolState:
        """Get or create state for symbol."""
        if symbol not in self._states:
            self._states[symbol] = SymbolState(symbol=symbol)
        return self._states[symbol]

    def on_trade(self, trade: dict) -> None:
        """Process incoming trade from WebSocket."""
        try:
            symbol = trade.get("s", "")
            if not symbol:
                return
            ts = int(trade.get("T", 0))
            side = trade.get("S", "")
            size = float(trade.get("v", 0) or 0)
            price = float(trade.get("p", 0) or 0)
            tick_dir = trade.get("L", "")

            if size <= 0 or price <= 0:
                return

            state = self.ensure_symbol(symbol)
            state.last_price = price
            state.max_ts_ms = max(state.max_ts_ms, ts)

            # Aggressive: Buy = taker bought (aggressive buy), Sell = taker sold
            rec = TradeRecord(ts=ts, symbol=symbol, side=side, size=size, price=price, tick_direction=tick_dir)
            state.trades_5m.append(rec)

            cutoff = ts - self._cutoff_5m_ms
            # Trim old trades
            while state.trades_5m and state.trades_5m[0].ts < cutoff:
                state.trades_5m.popleft()

            # Recompute aggregates
            self._recompute_aggregates(state)
        except Exception as e:
            log.debug(f"Trade parse error: {e}")

    def _recompute_aggregates(self, state: SymbolState) -> None:
        """Recompute rolling buy/sell/delta/CVD from trade buffer."""
        now = state.max_ts_ms
        w30 = now - self.config.window_30s * 1000
        w1 = now - self.config.window_1m * 1000
        w3 = now - self.config.window_3m * 1000

        buy_30, sell_30 = 0.0, 0.0
        buy_1, sell_1 = 0.0, 0.0
        buy_3, sell_3 = 0.0, 0.0
        count_30, count_1, count_3 = 0, 0, 0
        cvd = 0.0
        vwap_num, vwap_den = 0.0, 0.0

        for t in state.trades_5m:
            vol = t.size * t.price
            if t.side == "Buy":
                delta = vol
            else:
                delta = -vol
            cvd += delta
            vwap_num += vol * t.price
            vwap_den += vol

            if t.ts >= w30:
                count_30 += 1
                if t.side == "Buy":
                    buy_30 += vol
                else:
                    sell_30 += vol
            if t.ts >= w1:
                count_1 += 1
                if t.side == "Buy":
                    buy_1 += vol
                else:
                    sell_1 += vol
            if t.ts >= w3:
                count_3 += 1
                if t.side == "Buy":
                    buy_3 += vol
                else:
                    sell_3 += vol

        state.buy_vol_30s = buy_30
        state.sell_vol_30s = sell_30
        state.buy_vol_1m = buy_1
        state.sell_vol_1m = sell_1
        state.buy_vol_3m = buy_3
        state.sell_vol_3m = sell_3
        state.delta_30s = buy_30 - sell_30
        state.delta_1m = buy_1 - sell_1
        state.delta_3m = buy_3 - sell_3
        state.cvd = cvd
        state.vwap = vwap_num / vwap_den if vwap_den > 0 else state.last_price
        state.vwap_vol = vwap_den
        state.trade_count_30s = count_30
        state.trade_count_1m = count_1
        state.trade_count_3m = count_3

    def update_ticker(self, symbol: str, bid: float, ask: float, last: float) -> None:
        """Update spread from ticker."""
        state = self.ensure_symbol(symbol)
        state.last_price = last
        if last > 0 and bid > 0 and ask > 0:
            state.spread_bps = (ask - bid) / last * 10_000

    def update_funding(self, symbol: str, rate: float) -> None:
        """Update funding rate."""
        self.ensure_symbol(symbol).funding_rate = rate

    def update_oi(self, symbol: str, oi: float, prev_oi: Optional[float] = None) -> None:
        """Update open interest."""
        state = self.ensure_symbol(symbol)
        if prev_oi is not None and prev_oi > 0:
            state.oi_change = (oi - prev_oi) / prev_oi
        state.open_interest = oi

    def update_long_short_ratio(self, symbol: str, ratio: float) -> None:
        """Update long/short ratio."""
        self.ensure_symbol(symbol).long_short_ratio = ratio

    def update_klines(self, symbol: str, closes: list[float], highs: list[float], lows: list[float]) -> None:
        """Update OHLC for ATR/returns."""
        state = self.ensure_symbol(symbol)
        for c, h, l in zip(closes, highs, lows):
            state.closes.append(c)
            state.highs.append(h)
            state.lows.append(l)

    def get_state(self, symbol: str) -> Optional[SymbolState]:
        """Get state for symbol."""
        return self._states.get(symbol)

    def get_all_states(self) -> dict[str, SymbolState]:
        """Get all symbol states."""
        return self._states

    def get_last_update_ts(self, symbol: str) -> int:
        """Get last trade timestamp for staleness check."""
        s = self._states.get(symbol)
        return s.max_ts_ms if s else 0
