"""Execution audit: intended vs actual (entry intent -> order ack -> fill)."""

from typing import Optional

from src.utils.logging import get_logger

log = get_logger(__name__)


def record_entry_intent(
    db,
    ts_ms: int,
    symbol: str,
    side: str,
    intent_qty: float,
    intent_price: Optional[float],
    intent_stop: Optional[float],
    order_id: str,
    order_link_id: str,
    config_id: Optional[str] = None,
    strategy: Optional[str] = None,
) -> None:
    """Record entry intent and order ack (call after place_entry returns success)."""
    if not db:
        return
    try:
        db.insert_execution_audit(
            ts=ts_ms,
            symbol=symbol,
            side=side,
            intent_qty=intent_qty,
            intent_price=intent_price,
            intent_stop=intent_stop,
            order_id=order_id,
            order_link_id=order_link_id or None,
            ack_ts=ts_ms,
            config_id=config_id,
            strategy=strategy,
        )
    except Exception as e:
        log.debug(f"Execution audit record_entry_intent: {e}")


def record_fill(
    db,
    order_id: str,
    fill_qty: float,
    fill_price: float,
    fill_ts_ms: int,
    intent_price: Optional[float] = None,
    intent_qty: Optional[float] = None,
) -> None:
    """Update execution audit with fill; compute slippage and deltas if intent available."""
    if not db:
        return
    slippage_bps = None
    size_delta = None
    notional_delta = None
    mismatch_reason = None
    if intent_price is not None and intent_price > 0:
        slippage_bps = abs(fill_price - intent_price) / intent_price * 10000.0
    if intent_qty is not None:
        size_delta = fill_qty - intent_qty
        if intent_price is not None:
            notional_delta = fill_qty * fill_price - intent_qty * intent_price
    if intent_qty is not None and abs(size_delta or 0) > 0.001:
        mismatch_reason = "size_delta"
    try:
        db.update_execution_audit_on_fill(
            order_id=order_id,
            fill_qty=fill_qty,
            fill_price=fill_price,
            fill_ts=fill_ts_ms,
            slippage_bps=slippage_bps,
            size_delta=size_delta,
            notional_delta=notional_delta,
            mismatch_reason=mismatch_reason,
        )
    except Exception as e:
        log.debug(f"Execution audit record_fill: {e}")
