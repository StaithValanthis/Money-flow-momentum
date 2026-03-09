"""Universe discovery and filtering for Bybit linear USDT perpetuals."""

from typing import Optional

from src.config.config import UniverseConfig
from src.exchange.bybit_client import BybitClient
from src.utils.logging import get_logger

log = get_logger(__name__)


class UniverseManager:
    """Discover and maintain tradable symbol universe from Bybit."""

    def __init__(self, client: BybitClient, config: UniverseConfig):
        self.client = client
        self.config = config
        self._instruments: dict[str, dict] = {}
        self._symbols: list[str] = []

    def refresh(self) -> list[str]:
        """Fetch instruments from exchange and apply filters."""
        instruments = self.client.get_all_linear_instruments()
        tickers = self.client.get_tickers(category="linear")
        ticker_map = {t["symbol"]: t for t in tickers.get("result", {}).get("list", [])}

        filtered: list[str] = []
        for inst in instruments:
            symbol = inst.get("symbol", "")
            status = inst.get("status", "")
            quote = inst.get("quoteCoin", "")
            if status != self.config.status_filter:
                continue
            if quote != self.config.quote_coin:
                continue

            # Allowlist / blocklist
            if self.config.allowlist and symbol not in self.config.allowlist:
                continue
            if symbol in self.config.blocklist:
                continue

            # Liquidity: 24h turnover
            ticker = ticker_map.get(symbol, {})
            turnover = float(ticker.get("turnover24h", 0) or 0)
            if turnover < self.config.min_24h_turnover_usdt:
                continue

            # Spread: use bid/ask if available
            bid = float(ticker.get("bid1Price", 0) or 0)
            ask = float(ticker.get("ask1Price", 0) or 0)
            last = float(ticker.get("lastPrice", 1) or 1)
            if last > 0 and bid > 0 and ask > 0:
                spread_bps = (ask - bid) / last * 10_000
                if spread_bps > self.config.max_spread_bps:
                    continue

            # Min notional
            lot_filter = inst.get("lotSizeFilter", {})
            min_notional = float(lot_filter.get("minNotionalValue", 0) or 0)
            if min_notional > self.config.min_notional_usdt:
                continue

            self._instruments[symbol] = {**inst, "ticker": ticker}
            filtered.append(symbol)

        self._symbols = sorted(filtered)
        log.info(f"Universe refreshed: {len(self._symbols)} symbols")
        return self._symbols

    @property
    def symbols(self) -> list[str]:
        """Current tradable symbols."""
        return self._symbols

    def get_instrument(self, symbol: str) -> Optional[dict]:
        """Get instrument metadata for symbol."""
        return self._instruments.get(symbol)

    def get_tick_size(self, symbol: str) -> float:
        """Get price tick size."""
        inst = self._instruments.get(symbol)
        if not inst:
            return 0.01
        pf = inst.get("priceFilter", {})
        return float(pf.get("tickSize", 0.01) or 0.01)

    def get_qty_step(self, symbol: str) -> float:
        """Get quantity step."""
        inst = self._instruments.get(symbol)
        if not inst:
            return 0.001
        lf = inst.get("lotSizeFilter", {})
        return float(lf.get("qtyStep", 0.001) or 0.001)

    def get_min_qty(self, symbol: str) -> float:
        """Get minimum order quantity."""
        inst = self._instruments.get(symbol)
        if not inst:
            return 0.001
        lf = inst.get("lotSizeFilter", {})
        return float(lf.get("minOrderQty", 0.001) or 0.001)

    def get_min_notional(self, symbol: str) -> float:
        """Get minimum notional value."""
        inst = self._instruments.get(symbol)
        if not inst:
            return 5.0
        lf = inst.get("lotSizeFilter", {})
        return float(lf.get("minNotionalValue", 5) or 5)
