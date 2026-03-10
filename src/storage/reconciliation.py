"""Reconciliation store: orders, fills, positions from private WS + REST."""

import time
from dataclasses import dataclass, field
from typing import Optional

from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class OrderRecord:
    order_id: str
    order_link_id: str
    symbol: str
    side: str
    qty: float
    price: float
    order_type: str
    reduce_only: bool
    status: str
    created_ts: int
    updated_ts: int
    cum_exec_qty: float = 0.0
    avg_price: str = ""


@dataclass
class FillRecord:
    exec_id: str
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    exec_ts: int
    closed_pnl: float = 0.0


@dataclass
class PositionRecord:
    symbol: str
    side: str
    size: float
    entry_price: float
    stop_loss: float
    take_profit: float
    updated_ts: int
    position_idx: int = 0


class ReconciliationStore:
    """In-memory store of orders, fills, positions; exchange is source of truth."""

    def __init__(self):
        self.orders: dict[str, OrderRecord] = {}
        self.fills: list[FillRecord] = []
        self.positions: dict[str, PositionRecord] = {}
        self._last_order_ts: int = 0
        self._last_position_ts: int = 0
        self._last_fill_ts: int = 0
        self._seen_exec_ids: set[str] = set()

    def on_order_update(self, data: dict) -> None:
        """Handle order update from private WS."""
        try:
            order_id = data.get("orderId", "")
            if not order_id:
                return
            ts = int(data.get("updatedTime", 0) or time.time() * 1000)
            self._last_order_ts = max(self._last_order_ts, ts)
            self.orders[order_id] = OrderRecord(
                order_id=order_id,
                order_link_id=data.get("orderLinkId", ""),
                symbol=data.get("symbol", ""),
                side=data.get("side", ""),
                qty=float(data.get("qty", 0) or 0),
                price=float(data.get("price", 0) or 0),
                order_type=data.get("orderType", ""),
                reduce_only=data.get("reduceOnly", False) or False,
                status=data.get("orderStatus", ""),
                created_ts=int(data.get("createdTime", 0) or 0),
                updated_ts=ts,
                cum_exec_qty=float(data.get("cumExecQty", 0) or 0),
                avg_price=data.get("avgPrice", "") or "",
            )
        except Exception as e:
            log.debug(f"Order update parse: {e}")

    def on_position_update(self, data: dict) -> None:
        """Handle position update from private WS."""
        try:
            symbol = data.get("symbol", "")
            if not symbol:
                return
            size = float(data.get("size", 0) or 0)
            ts = int(data.get("updatedTime", 0) or time.time() * 1000)
            self._last_position_ts = max(self._last_position_ts, ts)
            if size == 0:
                self.positions.pop(symbol, None)
                return
            self.positions[symbol] = PositionRecord(
                symbol=symbol,
                side=data.get("side", "Buy"),
                size=size,
                entry_price=float(data.get("avgPrice", 0) or 0),
                stop_loss=float(data.get("stopLoss", 0) or 0),
                take_profit=float(data.get("takeProfit", 0) or 0),
                updated_ts=ts,
                position_idx=int(data.get("positionIdx", 0) or 0),
            )
        except Exception as e:
            log.debug(f"Position update parse: {e}")

    def on_execution(self, data: dict) -> None:
        """Handle execution/fill from private WS."""
        try:
            exec_id = data.get("execId", "") or data.get("executionId", "")
            if not exec_id:
                return
            if exec_id in self._seen_exec_ids:
                # Duplicate / replayed execution event
                return
            self._seen_exec_ids.add(exec_id)
            ts = int(data.get("execTime", 0) or time.time() * 1000)
            self._last_fill_ts = max(self._last_fill_ts, ts)
            self.fills.append(
                FillRecord(
                    exec_id=exec_id,
                    order_id=data.get("orderId", ""),
                    symbol=data.get("symbol", ""),
                    side=data.get("side", ""),
                    qty=float(data.get("execQty", 0) or 0),
                    price=float(data.get("execPrice", 0) or 0),
                    exec_ts=ts,
                    closed_pnl=float(data.get("closedPnl", 0) or 0),
                )
            )
            # Keep last 1000 fills
            if len(self.fills) > 1000:
                self.fills = self.fills[-1000:]
        except Exception as e:
            log.debug(f"Execution parse: {e}")

    def sync_positions_from_rest(self, positions_list: list[dict]) -> None:
        """Replace positions from REST response."""
        self.positions.clear()
        for p in positions_list:
            symbol = p.get("symbol", "")
            size = float(p.get("size", 0) or 0)
            if not symbol:
                continue
            if size == 0:
                continue
            self.positions[symbol] = PositionRecord(
                symbol=symbol,
                side=p.get("side", "Buy"),
                size=size,
                entry_price=float(p.get("avgPrice", 0) or 0),
                stop_loss=float(p.get("stopLoss", 0) or 0),
                take_profit=float(p.get("takeProfit", 0) or 0),
                updated_ts=int(p.get("updatedTime", 0) or 0),
                position_idx=int(p.get("positionIdx", 0) or 0),
            )

    def get_position(self, symbol: str) -> Optional[PositionRecord]:
        return self.positions.get(symbol)

    def get_open_positions(self) -> list[PositionRecord]:
        return [p for p in self.positions.values() if p.size != 0]

    def last_order_update_ts(self) -> int:
        return self._last_order_ts

    def last_position_update_ts(self) -> int:
        return self._last_position_ts

    def last_fill_ts(self) -> int:
        return self._last_fill_ts

    def is_private_ws_stale(self, now_ms: int, timeout_ms: int) -> bool:
        """True if no order/position update within timeout."""
        return (now_ms - max(self._last_order_ts, self._last_position_ts)) > timeout_ms
