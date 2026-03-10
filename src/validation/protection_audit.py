"""Protection-state audit: intended SL/TP/breakeven vs exchange-reconciled state."""

import time
from typing import Optional

from src.utils.logging import get_logger

log = get_logger(__name__)


def run_protection_audit(
    db,
    reconciled_positions: list,
    lifecycle_get,
    config,
    *,
    config_id: Optional[str] = None,
    repair_missing: bool = False,
    executor_set_tp_sl=None,
) -> int:
    """
    Compare intended protection (from lifecycle) vs reconciled position state.
    reconciled_positions: list of objects with .symbol, .stop_loss, .take_profit
    lifecycle_get(symbol) -> lifecycle state or None
    Returns count of mismatches (non-repaired).
    """
    if not db:
        return 0
    now_ms = int(time.time() * 1000)
    mismatch_count = 0
    for pos in reconciled_positions:
        symbol = getattr(pos, "symbol", None) or (pos.get("symbol") if isinstance(pos, dict) else None)
        if not symbol:
            continue
        actual_sl = getattr(pos, "stop_loss", None)
        if actual_sl is None and isinstance(pos, dict):
            actual_sl = pos.get("stop_loss", 0)
        actual_tp = getattr(pos, "take_profit", None)
        if actual_tp is None and isinstance(pos, dict):
            actual_tp = pos.get("take_profit", 0)
        actual_sl = float(actual_sl or 0)
        actual_tp = float(actual_tp or 0)

        lc = lifecycle_get(symbol) if callable(lifecycle_get) else None
        if not lc:
            continue
        expected_sl = getattr(lc, "stop_loss", None) or 0
        expected_sl = float(expected_sl)
        repaired = False

        if expected_sl > 0 and actual_sl <= 0:
            db.insert_protection_audit(
                now_ms, symbol, "missing_stop_loss",
                expected_value=expected_sl, actual_value=actual_sl,
                repaired=False, message="Missing stop loss on exchange",
                config_id=config_id,
            )
            mismatch_count += 1
            if repair_missing and executor_set_tp_sl and callable(executor_set_tp_sl):
                if executor_set_tp_sl(symbol, None, expected_sl):
                    repaired = True
                    mismatch_count -= 1
                    db.insert_protection_audit(
                        now_ms, symbol, "missing_stop_loss",
                        expected_value=expected_sl, actual_value=expected_sl,
                        repaired=True, message="Repaired: set SL",
                        config_id=config_id,
                    )

        stop_moved = getattr(lc, "stop_moved_to_breakeven", False)
        if stop_moved and expected_sl > 0:
            be = getattr(lc, "trailing_stop_price", None) or 0
            if be > 0 and actual_sl <= 0:
                db.insert_protection_audit(
                    now_ms, symbol, "breakeven_not_applied",
                    expected_value=be, actual_value=actual_sl,
                    repaired=False, message="Breakeven intended but not on exchange",
                    config_id=config_id,
                )
                mismatch_count += 1

    return mismatch_count
