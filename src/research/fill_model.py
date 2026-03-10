"""Fill model: slippage, spread cost, entry/exit delay for replay/backtest."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class FillModelConfig:
    """Configurable fill/slippage assumptions."""

    slippage_bps: float = 20.0
    spread_cost_bps: float = 10.0
    entry_delay_bars: int = 0
    exit_delay_bars: int = 0
    partial_fill_pct: float = 1.0  # 1.0 = full fill


@dataclass
class FillResult:
    """Result of applying fill model."""

    fill_price: float
    fill_qty: float
    slippage_cost_usdt: float = 0.0
    spread_cost_usdt: float = 0.0
    delayed_ts: Optional[int] = None


def apply_slippage(
    side: str,
    ref_price: float,
    qty: float,
    slippage_bps: float,
) -> tuple[float, float]:
    """Return (fill_price, slippage_cost_usdt). Long = pay more, short = receive less."""
    if ref_price <= 0:
        return ref_price, 0.0
    pct = slippage_bps / 10_000
    if side == "Buy":
        fill_price = ref_price * (1 + pct)
    else:
        fill_price = ref_price * (1 - pct)
    slippage_cost = qty * abs(fill_price - ref_price)
    return fill_price, slippage_cost


def apply_spread_cost(
    notional_usdt: float,
    spread_bps: float,
) -> float:
    """Spread cost in USDT (half-spread each side approx)."""
    return notional_usdt * (spread_bps / 10_000) * 0.5


def fill_result(
    side: str,
    ref_price: float,
    qty: float,
    config: FillModelConfig,
) -> FillResult:
    """Apply fill model: slippage + spread cost. No delay applied here (handled in runner)."""
    fill_price, slippage_cost = apply_slippage(side, ref_price, qty, config.slippage_bps)
    notional = qty * fill_price
    spread_cost = apply_spread_cost(notional, config.spread_cost_bps)
    actual_qty = qty * config.partial_fill_pct
    return FillResult(
        fill_price=fill_price,
        fill_qty=actual_qty,
        slippage_cost_usdt=slippage_cost,
        spread_cost_usdt=spread_cost,
    )
