"""Bybit V5 REST and WebSocket client with reconnect logic and rate limiting."""

import asyncio
import time
from typing import Any, Callable, Optional

from loguru import logger
from pybit.unified_trading import HTTP, WebSocket

from src.config.config import ExchangeConfig, EnvSettings
from src.utils.logging import get_logger

log = get_logger(__name__)

# Bybit V5 endpoints
REST_MAINNET = "https://api.bybit.com"
REST_TESTNET = "https://api-testnet.bybit.com"
REST_DEMO = "https://api-demo.bybit.com"
WS_PUBLIC_MAINNET = "wss://stream.bybit.com/v5/public/linear"
WS_PUBLIC_TESTNET = "wss://stream-testnet.bybit.com/v5/public/linear"
WS_PRIVATE_MAINNET = "wss://stream.bybit.com/v5/private"
WS_PRIVATE_TESTNET = "wss://stream-testnet.bybit.com/v5/private"
WS_PRIVATE_DEMO = "wss://stream-demo.bybit.com/v5/private"


class BybitClient:
    """Bybit V5 client: REST + public/private WebSockets. Demo mode: demo REST + demo private WS + mainnet public WS."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        demo: bool = False,
        config: Optional[ExchangeConfig] = None,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.demo = demo
        self.config = config or ExchangeConfig()
        self._http: Optional[HTTP] = None
        self._ws_public: Optional[WebSocket] = None
        self._ws_private: Optional[WebSocket] = None
        self._last_request_time = 0.0
        self._min_interval = 1.0 / self.config.rate_limit_requests_per_second

    @property
    def http(self) -> HTTP:
        """Lazy-initialize REST client. Demo uses api-demo.bybit.com; do not use testnet with demo."""
        if self._http is None:
            if self.demo:
                self._http = HTTP(
                    testnet=False,
                    demo=True,
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                )
            else:
                self._http = HTTP(
                    testnet=self.testnet,
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                )
        return self._http

    def _rate_limit(self) -> None:
        """Enforce rate limiting between REST calls."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()

    def _retry_rest(self, fn: Callable[[], Any], max_retries: int = 3) -> Any:
        """Execute REST call with retry and backoff."""
        last_err: Optional[Exception] = None
        for attempt in range(max_retries):
            self._rate_limit()
            try:
                result = fn()
                if hasattr(result, "get") and result.get("retCode") != 0:
                    raise RuntimeError(
                        f"Bybit API error: {result.get('retMsg', 'Unknown')} (code={result.get('retCode')})"
                    )
                return result
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    delay = 2 ** attempt
                    log.warning(f"REST retry {attempt + 1}/{max_retries} after {e}, sleeping {delay}s")
                    time.sleep(delay)
        raise last_err

    # --- REST: Market ---

    def get_instruments(self, category: str = "linear", cursor: Optional[str] = None) -> dict:
        """Fetch instrument info with pagination."""
        def _call():
            params: dict = {"category": category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            return self.http.get_instruments_info(**params)
        return self._retry_rest(_call)

    def get_all_linear_instruments(self) -> list[dict]:
        """Fetch all linear perpetual instruments (handles pagination)."""
        all_items: list[dict] = []
        cursor = None
        while True:
            resp = self.get_instruments(category="linear", cursor=cursor)
            result = resp.get("result", {})
            items = result.get("list", [])
            all_items.extend(items)
            cursor = result.get("nextPageCursor", "")
            if not cursor:
                break
        return all_items

    def get_tickers(self, category: str = "linear", symbol: Optional[str] = None) -> dict:
        """Fetch ticker data."""
        def _call():
            params: dict = {"category": category}
            if symbol:
                params["symbol"] = symbol
            return self.http.get_tickers(**params)
        return self._retry_rest(_call)

    def get_klines(
        self,
        category: str,
        symbol: str,
        interval: str = "1",
        start: Optional[int] = None,
        end: Optional[int] = None,
        limit: int = 200,
    ) -> dict:
        """Fetch klines. Interval: 1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M."""
        def _call():
            params: dict = {
                "category": category,
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            }
            if start:
                params["start"] = start
            if end:
                params["end"] = end
            return self.http.get_kline(**params)
        return self._retry_rest(_call)

    def get_funding_rate(self, category: str = "linear", symbol: Optional[str] = None) -> dict:
        """Fetch funding rate (from tickers)."""
        return self.get_tickers(category=category, symbol=symbol)

    def get_open_interest(self, category: str, symbol: str, interval: str = "5min") -> dict:
        """Fetch open interest."""
        def _call():
            return self.http.get_open_interest(
                category=category,
                symbol=symbol,
                intervalTime=interval,
            )
        return self._retry_rest(_call)

    def get_orderbook(self, category: str, symbol: str, limit: int = 25) -> dict:
        """Fetch orderbook."""
        def _call():
            return self.http.get_orderbook(category=category, symbol=symbol, limit=limit)
        return self._retry_rest(_call)

    def get_long_short_ratio(self, category: str = "linear", symbol: Optional[str] = None) -> dict:
        """Fetch long/short ratio (linear only)."""
        def _call():
            params: dict = {"category": category}
            if symbol:
                params["symbol"] = symbol
            return self.http.get_long_short_ratio(**params)
        return self._retry_rest(_call)

    # --- REST: Trading ---

    def set_leverage(self, category: str, symbol: str, buy_leverage: int, sell_leverage: int) -> dict:
        """Set leverage for symbol."""
        def _call():
            return self.http.set_leverage(
                category=category,
                symbol=symbol,
                buyLeverage=buy_leverage,
                sellLeverage=sell_leverage,
            )
        return self._retry_rest(_call)

    def set_position_mode(self, mode: int = 0) -> dict:
        """Set position mode. 0 = merged (one-way), 3 = both sides."""
        def _call():
            return self.http.switch_position_mode(
                category="linear",
                mode=mode,
                symbol="",
            )
        return self._retry_rest(_call)

    def place_order(
        self,
        category: str,
        symbol: str,
        side: str,
        order_type: str = "Market",
        qty: Optional[str] = None,
        price: Optional[str] = None,
        reduce_only: bool = False,
        order_link_id: Optional[str] = None,
        take_profit: Optional[str] = None,
        stop_loss: Optional[str] = None,
        time_in_force: str = "GTC",
        position_idx: int = 0,
    ) -> dict:
        """Place order."""
        def _call():
            params: dict = {
                "category": category,
                "symbol": symbol,
                "side": side,
                "orderType": order_type,
                "qty": str(qty) if qty else None,
                "reduceOnly": reduce_only,
                "positionIdx": position_idx,
            }
            if price:
                params["price"] = str(price)
            if order_link_id:
                params["orderLinkId"] = order_link_id
            if take_profit:
                params["takeProfit"] = str(take_profit)
            if stop_loss:
                params["stopLoss"] = str(stop_loss)
            if order_type == "Market":
                params["timeInForce"] = "GTC"
            params = {k: v for k, v in params.items() if v is not None}
            return self.http.place_order(**params)
        return self._retry_rest(_call)

    def set_trading_stop(
        self,
        category: str,
        symbol: str,
        position_idx: int = 0,
        take_profit: Optional[str] = None,
        stop_loss: Optional[str] = None,
    ) -> dict:
        """Set TP/SL for existing position."""
        def _call():
            params: dict = {"category": category, "symbol": symbol, "positionIdx": position_idx}
            if take_profit:
                params["takeProfit"] = str(take_profit)
            if stop_loss:
                params["stopLoss"] = str(stop_loss)
            return self.http.set_trading_stop(**params)
        return self._retry_rest(_call)

    def close_position(
        self,
        category: str,
        symbol: str,
        side: str,
        qty: str,
        position_idx: int = 0,
    ) -> dict:
        """Close position via reduce-only market order."""
        return self.place_order(
            category=category,
            symbol=symbol,
            side=side,
            order_type="Market",
            qty=qty,
            reduce_only=True,
            position_idx=position_idx,
        )

    def get_positions(self, category: str = "linear", symbol: Optional[str] = None) -> dict:
        """Get positions."""
        def _call():
            params: dict = {"category": category}
            if symbol:
                params["symbol"] = symbol
            return self.http.get_positions(**params)
        return self._retry_rest(_call)

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
        """Get wallet balance."""
        def _call():
            return self.http.get_wallet_balance(accountType=account_type)
        return self._retry_rest(_call)

    # --- WebSocket Public ---
    # Demo mode: use mainnet public WS (Bybit: demo public data = mainnet). Otherwise testnet/mainnet by flag.

    def start_public_ws(
        self,
        symbols: list[str],
        on_trade: Callable[[dict], None],
        on_ticker: Optional[Callable[[dict], None]] = None,
    ) -> WebSocket:
        """Start public WebSocket for trades and optionally tickers. In demo mode uses mainnet public stream."""
        def _trade_handler(msg: dict) -> None:
            try:
                data = msg.get("data", [])
                if not isinstance(data, list):
                    data = [data] if data else []
                for t in data:
                    on_trade(t)
            except Exception as e:
                log.error(f"WS trade handler error: {e}")

        def _ticker_handler(msg: dict) -> None:
            try:
                if on_ticker:
                    data = msg.get("data", [])
                    if not isinstance(data, list):
                        data = [data] if data else []
                    for t in data:
                        on_ticker(t)
            except Exception as e:
                log.error(f"WS ticker handler error: {e}")

        use_mainnet_public = self.demo  # demo: mainnet public data
        self._ws_public = WebSocket(
            testnet=False if use_mainnet_public else self.testnet,
            channel_type="linear",
            demo=False if use_mainnet_public else False,
        )
        sym_arg = symbols if len(symbols) <= 50 else symbols[:50]
        self._ws_public.trade_stream(symbol=sym_arg, callback=_trade_handler)
        if on_ticker and sym_arg:
            self._ws_public.ticker_stream(symbol=sym_arg, callback=_ticker_handler)
        return self._ws_public

    def start_private_ws(
        self,
        on_order: Callable[[dict], None],
        on_position: Callable[[dict], None],
        on_execution: Optional[Callable[[dict], None]] = None,
    ) -> WebSocket:
        """Start private WebSocket for orders, positions, executions. Demo uses stream-demo.bybit.com."""
        self._ws_private = WebSocket(
            testnet=False if self.demo else self.testnet,
            channel_type="private",
            demo=self.demo,
            api_key=self.api_key,
            api_secret=self.api_secret,
        )

        def _order_handler(msg: dict) -> None:
            try:
                data = msg.get("data", [])
                if not isinstance(data, list):
                    data = [data] if data else []
                for o in data:
                    on_order(o)
            except Exception as e:
                log.error(f"WS order handler error: {e}")

        def _position_handler(msg: dict) -> None:
            try:
                data = msg.get("data", [])
                if not isinstance(data, list):
                    data = [data] if data else []
                for p in data:
                    on_position(p)
            except Exception as e:
                log.error(f"WS position handler error: {e}")

        def _execution_handler(msg: dict) -> None:
            try:
                if on_execution:
                    data = msg.get("data", [])
                    if not isinstance(data, list):
                        data = [data] if data else []
                    for e in data:
                        on_execution(e)
            except Exception as e:
                log.error(f"WS execution handler error: {e}")

        self._ws_private.order_stream(callback=_order_handler)
        self._ws_private.position_stream(callback=_position_handler)
        if on_execution:
            self._ws_private.execution_stream(callback=_execution_handler)
        return self._ws_private

    def stop_public_ws(self) -> None:
        """Stop public WebSocket."""
        if self._ws_public:
            try:
                self._ws_public.exit()
            except Exception:
                pass
            self._ws_public = None

    def stop_private_ws(self) -> None:
        """Stop private WebSocket."""
        if self._ws_private:
            try:
                self._ws_private.exit()
            except Exception:
                pass
            self._ws_private = None
