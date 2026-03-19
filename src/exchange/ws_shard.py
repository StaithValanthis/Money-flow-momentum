"""Shard public trade/ticker WebSocket across multiple connections."""

import threading
import time
from typing import Callable, Optional

from pybit.unified_trading import WebSocket

from src.exchange.pybit_ws_ping_guard import install_pybit_ws_ping_guard
from src.utils.logging import get_logger

install_pybit_ws_ping_guard()

log = get_logger(__name__)


class PublicWSShard:
    """Single public WS connection for a subset of symbols."""

    def __init__(
        self,
        shard_id: int,
        symbols: list[str],
        testnet: bool,
        on_trade: Callable[[dict], None],
        on_ticker: Optional[Callable[[dict], None]] = None,
    ):
        self.shard_id = shard_id
        self.symbols = symbols
        self.testnet = testnet
        self.on_trade = on_trade
        self.on_ticker = on_ticker
        self._ws: Optional[WebSocket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._last_msg_ts: int = 0
        self._last_reconnect_ts: float = 0.0

    def _trade_handler(self, msg: dict) -> None:
        try:
            self._last_msg_ts = int(time.time() * 1000)
            data = msg.get("data", [])
            if not isinstance(data, list):
                data = [data] if data else []
            for t in data:
                self.on_trade(t)
        except Exception as e:
            log.error(f"Shard {self.shard_id} trade handler: {e}")

    def _ticker_handler(self, msg: dict) -> None:
        try:
            if self.on_ticker:
                data = msg.get("data", [])
                if not isinstance(data, list):
                    data = [data] if data else []
                for t in data:
                    self.on_ticker(t)
        except Exception as e:
            log.error(f"Shard {self.shard_id} ticker handler: {e}")

    def start(self) -> None:
        if not self.symbols:
            return
        self._running = True
        self._ws = WebSocket(testnet=self.testnet, channel_type="linear")
        self._ws.trade_stream(symbol=self.symbols, callback=self._trade_handler)
        if self.on_ticker:
            self._ws.ticker_stream(symbol=self.symbols, callback=self._ticker_handler)
        log.info(f"Public WS shard {self.shard_id} started for {len(self.symbols)} symbols")

    def run_in_thread(self) -> None:
        """Run this shard in a daemon thread (pybit blocks)."""
        def _run():
            self.start()
            while self._running:
                time.sleep(1)
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._ws:
            log.info("Public websocket shard {} stopping", self.shard_id)
            try:
                self._ws.exit()
            except Exception:
                pass
            self._ws = None
        log.info("Public websocket shard {} stopped", self.shard_id)

    def last_message_ts(self) -> int:
        return self._last_msg_ts

    def reconnect_if_stale(self, now_ms: int, timeout_ms: int, backoff_seconds: float) -> None:
        """Reconnect this shard if it is stale and backoff has elapsed."""
        last = self.last_message_ts()
        if last <= 0:
            return
        age = now_ms - last
        if age <= timeout_ms:
            return
        now = time.time()
        if now - self._last_reconnect_ts < backoff_seconds:
            return
        self._last_reconnect_ts = now
        log.warning(
            f"Shard {self.shard_id} stale for {age}ms (> {timeout_ms}ms); reconnecting"
        )
        # Stop existing WS and start a new one
        self.stop()
        # Small delay before reconnect
        time.sleep(1)
        self.run_in_thread()


class PublicWSShardManager:
    """Manage multiple public WS shards for full universe."""

    def __init__(
        self,
        symbols: list[str],
        max_symbols_per_connection: int,
        testnet: bool,
        on_trade: Callable[[dict], None],
        on_ticker: Optional[Callable[[dict], None]] = None,
    ):
        self.symbols = symbols
        self.max_per_conn = max_symbols_per_connection
        self.testnet = testnet
        self.on_trade = on_trade
        self.on_ticker = on_ticker
        self._shards: list[PublicWSShard] = []
        self._symbol_to_shard: dict[str, int] = {}

    def build_shards(self) -> list[PublicWSShard]:
        """Split symbols into chunks and create shards."""
        self._shards.clear()
        self._symbol_to_shard.clear()
        for i in range(0, len(self.symbols), self.max_per_conn):
            chunk = self.symbols[i : i + self.max_per_conn]
            shard_id = len(self._shards)
            shard = PublicWSShard(
                shard_id=shard_id,
                symbols=chunk,
                testnet=self.testnet,
                on_trade=self.on_trade,
                on_ticker=self.on_ticker,
            )
            self._shards.append(shard)
            for s in chunk:
                self._symbol_to_shard[s] = shard_id
        log.info(f"Built {len(self._shards)} public WS shards for {len(self.symbols)} symbols")
        return self._shards

    def start_all(self) -> None:
        for shard in self._shards:
            shard.run_in_thread()
        time.sleep(2)

    def stop_all(self) -> None:
        for shard in self._shards:
            shard.stop()

    def is_any_stale(self, now_ms: int, timeout_ms: int) -> bool:
        """True if any shard has not received a message within timeout."""
        for shard in self._shards:
            if shard.last_message_ts() > 0 and (now_ms - shard.last_message_ts()) > timeout_ms:
                return True
        return False

    def shard_for_symbol(self, symbol: str) -> Optional[int]:
        return self._symbol_to_shard.get(symbol)

    def monitor_and_reconnect(self, now_ms: int, timeout_ms: int, backoff_seconds: float) -> None:
        """Check each shard for staleness and reconnect if needed."""
        for shard in self._shards:
            shard.reconnect_if_stale(now_ms, timeout_ms, backoff_seconds)

    def refresh_symbols(self, symbols: list[str]) -> None:
        """Rebuild shards if symbol set changes materially."""
        if symbols == self.symbols:
            return
        log.info(
            f"Refreshing WS shards for updated universe: {len(self.symbols)} -> {len(symbols)} symbols"
        )
        self.stop_all()
        self.symbols = symbols
        self.build_shards()
        self.start_all()