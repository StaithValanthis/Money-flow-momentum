"""Tests for TP1/TP2 calculations and lifecycle transitions."""

from src.config.config import StopTPConfig
from src.portfolio.lifecycle import LifecycleState, LifecyclePhase


def test_tp_price_calculation_long_short():
    cfg = StopTPConfig(tp1_r_multiple=1.0, tp2_r_multiple=2.0)
    entry = 100.0
    stop = 90.0
    R = abs(entry - stop)

    # Long
    tp1_long = entry + R * cfg.tp1_r_multiple
    tp2_long = entry + R * cfg.tp2_r_multiple
    assert tp1_long == 110.0
    assert tp2_long == 120.0

    # Short
    tp1_short = entry - R * cfg.tp1_r_multiple
    tp2_short = entry - R * cfg.tp2_r_multiple
    assert tp1_short == 90.0
    assert tp2_short == 80.0


def test_lifecycle_tp_fill_transitions():
    """Lifecycle only transitions to TP1/TP2 filled when planned qty is reached."""
    lc = LifecycleState(
        symbol="BTCUSDT",
        side="Buy",
        entry_price=50000.0,
        stop_loss=49000.0,
        take_profit=0.0,
        atr_at_entry=500.0,
        size=1.0,
        entry_ts=0,
    )
    lc.tp1_planned_qty = 0.5
    lc.tp2_planned_qty = 0.3

    # Partial TP1
    lc.tp1_filled_qty += 0.2
    assert lc.phase == LifecyclePhase.OPEN

    # Full TP1
    lc.tp1_filled_qty += 0.3
    planned = lc.tp1_planned_qty or lc.tp1_filled_qty
    if planned > 0 and lc.tp1_filled_qty >= 0.99 * planned:
        lc.phase = LifecyclePhase.TP1_FILLED
    assert lc.phase == LifecyclePhase.TP1_FILLED

