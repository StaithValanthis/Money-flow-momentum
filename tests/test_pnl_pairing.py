"""Tests for realized PnL pairing (entry/exit) and evaluator PnL resolution."""

import pytest

from src.evaluation.datasets import (
    compute_realized_pnl_by_pairing,
    _is_entry_row,
    _is_exit_row,
)


def test_is_entry_exit_classification() -> None:
    assert _is_entry_row({"order_link_id": "entry_flow_123"}) is True
    assert _is_entry_row({"order_link_id": "entry"}) is True
    assert _is_entry_row({"order_link_id": "tp1_abc"}) is False
    assert _is_exit_row({"order_link_id": "tp1_abc"}) is True
    assert _is_exit_row({"order_link_id": "tp2_xyz"}) is True
    assert _is_exit_row({"order_link_id": "entry_flow"}) is False


def test_pairing_long_entry_exit_positive_pnl() -> None:
    """Long: Buy entry then Sell exit at higher price => positive PnL."""
    trades = [
        {"ts": 1000, "symbol": "BTCUSDT", "side": "Buy", "qty": 0.01, "price": 50_000.0, "order_link_id": "entry_abc", "pnl": 0.0},
        {"ts": 2000, "symbol": "BTCUSDT", "side": "Sell", "qty": 0.01, "price": 51_000.0, "order_link_id": "tp1_xyz", "pnl": 0.0},
    ]
    out = compute_realized_pnl_by_pairing(trades)
    assert out[0].get("pnl") == 0.0
    assert out[1].get("pnl") == pytest.approx(10.0)  # (51000 - 50000) * 0.01


def test_pairing_long_entry_exit_negative_pnl() -> None:
    """Long: Buy entry then Sell exit at lower price => negative PnL."""
    trades = [
        {"ts": 1000, "symbol": "BTCUSDT", "side": "Buy", "qty": 0.01, "price": 50_000.0, "order_link_id": "entry_abc", "pnl": 0.0},
        {"ts": 2000, "symbol": "BTCUSDT", "side": "Sell", "qty": 0.01, "price": 49_000.0, "order_link_id": "tp1_xyz", "pnl": 0.0},
    ]
    out = compute_realized_pnl_by_pairing(trades)
    assert out[1].get("pnl") == pytest.approx(-10.0)


def test_pairing_short_entry_exit_positive_pnl() -> None:
    """Short: Sell entry then Buy exit at lower price => positive PnL."""
    trades = [
        {"ts": 1000, "symbol": "ETHUSDT", "side": "Sell", "qty": 0.1, "price": 3_000.0, "order_link_id": "entry_def", "pnl": 0.0},
        {"ts": 2000, "symbol": "ETHUSDT", "side": "Buy", "qty": 0.1, "price": 2_900.0, "order_link_id": "tp1_uvw", "pnl": 0.0},
    ]
    out = compute_realized_pnl_by_pairing(trades)
    assert out[1].get("pnl") == pytest.approx(10.0)  # (3000 - 2900) * 0.1


def test_pairing_short_entry_exit_negative_pnl() -> None:
    """Short: Sell entry then Buy exit at higher price => negative PnL."""
    trades = [
        {"ts": 1000, "symbol": "ETHUSDT", "side": "Sell", "qty": 0.1, "price": 3_000.0, "order_link_id": "entry_def", "pnl": 0.0},
        {"ts": 2000, "symbol": "ETHUSDT", "side": "Buy", "qty": 0.1, "price": 3_100.0, "order_link_id": "tp2_uvw", "pnl": 0.0},
    ]
    out = compute_realized_pnl_by_pairing(trades)
    assert out[1].get("pnl") == pytest.approx(-10.0)


def test_pairing_partial_tp_exits() -> None:
    """Entry 0.03, tp1 exit 0.01, tp2 exit 0.02 => two exit rows with correct PnL."""
    trades = [
        {"ts": 1000, "symbol": "BTCUSDT", "side": "Buy", "qty": 0.03, "price": 50_000.0, "order_link_id": "entry_1", "pnl": 0.0},
        {"ts": 2000, "symbol": "BTCUSDT", "side": "Sell", "qty": 0.01, "price": 51_000.0, "order_link_id": "tp1_a", "pnl": 0.0},
        {"ts": 3000, "symbol": "BTCUSDT", "side": "Sell", "qty": 0.02, "price": 52_000.0, "order_link_id": "tp2_b", "pnl": 0.0},
    ]
    out = compute_realized_pnl_by_pairing(trades)
    assert out[1].get("pnl") == pytest.approx(10.0)   # 0.01 * (51000 - 50000)
    assert out[2].get("pnl") == pytest.approx(40.0)   # 0.02 * (52000 - 50000)


def test_pairing_empty_returns_empty() -> None:
    assert compute_realized_pnl_by_pairing([]) == []


def test_pairing_does_not_mutate_input() -> None:
    trades = [
        {"ts": 1000, "symbol": "X", "side": "Buy", "qty": 1, "price": 100.0, "order_link_id": "entry_x", "pnl": 0.0},
        {"ts": 2000, "symbol": "X", "side": "Sell", "qty": 1, "price": 110.0, "order_link_id": "tp1_x", "pnl": 0.0},
    ]
    out = compute_realized_pnl_by_pairing(trades)
    assert trades[0]["pnl"] == 0.0
    assert trades[1]["pnl"] == 0.0
    assert out[1]["pnl"] == pytest.approx(10.0)
