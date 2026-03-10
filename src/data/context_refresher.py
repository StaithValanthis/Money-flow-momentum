"""Scheduled context refresh: klines, OI, funding, long/short ratio, instruments."""

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.config.config import Config
from src.exchange.bybit_client import BybitClient
from src.data.market_state import MarketStateManager
from src.data.universe import UniverseManager
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ContextStaleness:
    """Last successful refresh timestamps (ms)."""

    klines: dict[str, int] = field(default_factory=dict)
    oi: dict[str, int] = field(default_factory=dict)
    funding: dict[str, int] = field(default_factory=dict)
    long_short_ratio: dict[str, int] = field(default_factory=dict)
    instruments: int = 0


class ContextRefresher:
    """Refresh slower context data on schedule; track staleness."""

    def __init__(
        self,
        client: BybitClient,
        config: Config,
        market_state: MarketStateManager,
        universe: UniverseManager,
        on_heartbeat: Optional[Callable[[str, bool], None]] = None,
    ):
        self.client = client
        self.config = config
        self.market_state = market_state
        self.universe = universe
        self.on_heartbeat = on_heartbeat
        self.staleness = ContextStaleness()
        self._running = False
        self._prev_oi: dict[str, float] = {}

    def _heartbeat(self, source: str, ok: bool) -> None:
        if self.on_heartbeat:
            self.on_heartbeat(source, ok)

    def refresh_klines(self, symbols: list[str]) -> None:
        """Fetch 1m klines and push to market state."""
        now_ms = int(time.time() * 1000)
        for symbol in symbols[:100]:
            try:
                resp = self.client.get_klines(
                    category="linear",
                    symbol=symbol,
                    interval="1",
                    limit=min(100, self.config.features.atr_period + 20),
                )
                lst = resp.get("result", {}).get("list", [])
                if not lst:
                    continue
                closes, highs, lows = [], [], []
                for item in reversed(lst):
                    if isinstance(item, list) and len(item) >= 5:
                        closes.append(float(item[4]))
                        highs.append(float(item[2]))
                        lows.append(float(item[3]))
                    elif isinstance(item, dict):
                        c = item.get("c") or item.get("close")
                        h = item.get("h") or item.get("high")
                        l = item.get("l") or item.get("low")
                        if c is not None:
                            closes.append(float(c))
                            highs.append(float(h) if h is not None else float(c))
                            lows.append(float(l) if l is not None else float(c))
                if closes:
                    self.market_state.update_klines(symbol, closes, highs, lows)
                    self.staleness.klines[symbol] = now_ms
            except Exception as e:
                log.debug(f"Kline refresh {symbol}: {e}")
                self._heartbeat("klines", False)
                return
        self._heartbeat("klines", True)

    def refresh_oi(self, symbols: list[str]) -> None:
        """Fetch open interest and update market state."""
        now_ms = int(time.time() * 1000)
        for symbol in symbols[:50]:
            try:
                resp = self.client.get_open_interest(
                    category="linear",
                    symbol=symbol,
                    interval="5min",
                )
                result = resp.get("result", {})
                lst = result.get("list", [])
                if lst:
                    item = lst[0] if isinstance(lst[0], dict) else {}
                    oi_val = float(item.get("openInterest", 0) or 0)
                    prev = self._prev_oi.get(symbol)
                    self.market_state.update_oi(symbol, oi_val, prev)
                    self._prev_oi[symbol] = oi_val
                    self.staleness.oi[symbol] = now_ms
            except Exception as e:
                log.debug(f"OI refresh {symbol}: {e}")
                self._heartbeat("oi", False)
                return
        self._heartbeat("oi", True)

    def refresh_funding(self, symbols: list[str]) -> None:
        """Fetch funding from tickers."""
        now_ms = int(time.time() * 1000)
        try:
            resp = self.client.get_tickers(category="linear")
            lst = resp.get("result", {}).get("list", [])
            for item in lst:
                sym = item.get("symbol", "")
                if sym not in symbols and symbols:
                    continue
                fr = item.get("fundingRate") or item.get("lastFundingRate")
                if fr is not None:
                    self.market_state.update_funding(sym, float(fr))
                    self.staleness.funding[sym] = now_ms
            self._heartbeat("funding", True)
        except Exception as e:
            log.debug(f"Funding refresh: {e}")
            self._heartbeat("funding", False)

    def refresh_long_short_ratio(self, symbols: list[str]) -> None:
        """Fetch long/short ratio per symbol. Skips symbols that fail (e.g. unsupported); period=5min required by API."""
        now_ms = int(time.time() * 1000)
        any_ok = False
        for symbol in symbols[:50]:
            try:
                resp = self.client.get_long_short_ratio(category="linear", symbol=symbol, period="5min")
                result = resp.get("result", {})
                lst = result.get("list", [])
                if lst:
                    item = lst[0] if isinstance(lst[0], dict) else {}
                    ratio = float(item.get("buySellRatio", 1) or 1)
                    self.market_state.update_long_short_ratio(symbol, ratio)
                    self.staleness.long_short_ratio[symbol] = now_ms
                    any_ok = True
            except Exception as e:
                log.debug("Long/short ratio {}: {}", symbol, e)
                continue
        self._heartbeat("long_short_ratio", any_ok)

    def refresh_instruments(self, universe: UniverseManager) -> None:
        """Refresh instrument metadata (universe)."""
        try:
            universe.refresh()
            self.staleness.instruments = int(time.time() * 1000)
            self._heartbeat("instruments", True)
        except Exception as e:
            log.warning(f"Instrument refresh: {e}")
            self._heartbeat("instruments", False)

    def is_symbol_context_fresh(self, symbol: str, now_ms: int) -> tuple[bool, str]:
        """Return (True, '') if context is fresh; else (False, reason)."""
        threshold_ms = int(self.config.context_staleness_seconds * 1000)
        if symbol not in self.staleness.klines or (now_ms - self.staleness.klines[symbol]) > threshold_ms:
            return False, "klines_stale"
        if symbol not in self.staleness.funding and symbol in self.universe.symbols:
            return False, "funding_stale"
        return True, ""
