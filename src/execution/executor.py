"""Order execution: place orders, TP/SL, reduce-only exits."""

import time
import uuid
from typing import Optional

from src.config.config import ExecutionConfig, StopTPConfig
from src.exchange.bybit_client import BybitClient
from src.utils.logging import get_logger

log = get_logger(__name__)


class Executor:
    """Execute orders with slippage protection and TP/SL."""

    def __init__(
        self,
        client: BybitClient,
        exec_config: ExecutionConfig,
        stop_tp_config: StopTPConfig,
    ):
        self.client = client
        self.exec_config = exec_config
        self.stop_tp_config = stop_tp_config

    def _order_link_id(self, prefix: str = "flow") -> str:
        """Generate idempotent order link ID."""
        if self.exec_config.idempotent_order_link:
            return f"{prefix}_{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}"
        return ""

    def place_reduce_only_tp(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        label: str,
    ) -> Optional[dict]:
        """
        Place a reduce-only TP order.

        side: close side (Sell for longs, Buy for shorts)
        label: e.g. "tp1" or "tp2" (used in orderLinkId prefix)
        """
        if qty <= 0:
            log.error(f"TP {label} qty <= 0 for {symbol}, skipping")
            return None

        order_link_id = self._order_link_id(f"{label}_{symbol}")

        try:
            result = self.client.place_order(
                category="linear",
                symbol=symbol,
                side=side,
                order_type="Limit",
                qty=str(round(qty, 8)),
                price=str(price),
                reduce_only=True,
                order_link_id=order_link_id,
            )
            if result.get("retCode") == 0:
                order = result.get("result", {}) or {}
                log.info(
                    f"TP {label} placed: {symbol} {side} qty={qty} "
                    f"price={price} orderId={order.get('orderId')}"
                )
                return order
            log.error(
                f"TP {label} failed for {symbol}: {result.get('retMsg')} "
                f"(code={result.get('retCode')})"
            )
            return None
        except Exception as e:
            log.error(f"TP {label} error for {symbol}: {e}")
            return None

    def place_entry(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Optional[dict]:
        """Place entry order."""
        order_type = "Limit" if self.exec_config.post_only_limit and price else "Market"
        order_link_id = self._order_link_id("entry") or None

        try:
            result = self.client.place_order(
                category="linear",
                symbol=symbol,
                side=side,
                order_type=order_type,
                qty=str(round(qty, 8)),
                price=str(price) if price else None,
                reduce_only=False,
                order_link_id=order_link_id,
                take_profit=str(take_profit) if take_profit else None,
                stop_loss=str(stop_loss) if stop_loss else None,
            )
            if result.get("retCode") == 0:
                log.info(f"Entry placed: {symbol} {side} qty={qty} orderId={result.get('result', {}).get('orderId')}")
                return result.get("result", {})
            log.error(f"Entry failed: {result.get('retMsg')}")
            return None
        except Exception as e:
            log.error(f"Entry error: {e}")
            return None

    def set_tp_sl(self, symbol: str, take_profit: Optional[float], stop_loss: Optional[float]) -> bool:
        """Set TP/SL on existing position."""
        try:
            result = self.client.set_trading_stop(
                category="linear",
                symbol=symbol,
                take_profit=str(take_profit) if take_profit else None,
                stop_loss=str(stop_loss) if stop_loss else None,
            )
            if result.get("retCode") == 0:
                log.info(f"TP/SL set: {symbol} TP={take_profit} SL={stop_loss}")
                return True
            log.error(f"Set TP/SL failed: {result.get('retMsg')}")
            return False
        except Exception as e:
            log.error(f"Set TP/SL error: {e}")
            return False

    def close_position(self, symbol: str, qty: Optional[float] = None, side: str = "Sell") -> bool:
        """Close position (reduce-only market order). Side = opposite of position."""
        if qty is None or qty <= 0:
            log.error("close_position requires qty > 0")
            return False
        try:
            result = self.client.close_position(
                category="linear",
                symbol=symbol,
                side=side,
                qty=str(round(qty, 8)),
            )
            if result.get("retCode") == 0:
                log.info(f"Position closed: {symbol}")
                return True
            log.error(f"Close failed: {result.get('retMsg')}")
            return False
        except Exception as e:
            log.error(f"Close error: {e}")
            return False

    def emergency_flatten(self, positions: list[dict]) -> dict[str, bool]:
        """Close all positions."""
        results = {}
        for pos in positions:
            sym = pos.get("symbol", "")
            size = float(pos.get("size", 0) or 0)
            side = "Sell" if size > 0 else "Buy"
            if sym and abs(size) > 0:
                results[sym] = self.close_position(sym, abs(size), side=side)
        return results
