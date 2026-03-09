"""Risk engine: position sizing, kill switch, circuit breakers."""

from dataclasses import dataclass
from typing import Optional

from src.config.config import RiskConfig
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class PositionSizingResult:
    """Result of position sizing."""

    qty: float
    notional_usdt: float
    risk_usdt: float
    stop_price: float
    r_multiple: float
    reject_reason: Optional[str] = None


class RiskEngine:
    """Risk management: sizing, limits, kill switch."""

    def __init__(self, config: RiskConfig, equity_usdt: float = 10_000.0):
        self.config = config
        self.equity_usdt = equity_usdt
        self.daily_pnl_start: Optional[float] = None
        self.kill_switch_triggered = False

    def set_equity(self, equity_usdt: float) -> None:
        """Update equity for sizing."""
        self.equity_usdt = equity_usdt

    def set_daily_start_pnl(self, pnl: float) -> None:
        """Set starting PnL for daily drawdown calc."""
        self.daily_pnl_start = pnl

    def compute_position_size(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_price: float,
        qty_step: float,
        min_qty: float,
        min_notional: float,
        max_notional: float,
    ) -> PositionSizingResult:
        """Compute position size from risk per trade."""
        if entry_price <= 0 or qty_step <= 0:
            return PositionSizingResult(
                qty=0, notional_usdt=0, risk_usdt=0, stop_price=stop_price, r_multiple=0,
                reject_reason="Invalid price or qty_step",
            )

        risk_per_trade = self.equity_usdt * (self.config.risk_per_trade_pct / 100)
        distance = abs(entry_price - stop_price)
        if distance <= 0:
            return PositionSizingResult(
                qty=0, notional_usdt=0, risk_usdt=0, stop_price=stop_price, r_multiple=0,
                reject_reason="Stop too close to entry",
            )

        r_multiple = distance / entry_price
        # qty such that risk = risk_per_trade: risk = qty * distance => qty = risk / distance
        qty = risk_per_trade / distance
        notional = qty * entry_price

        if notional < min_notional:
            return PositionSizingResult(
                qty=0, notional_usdt=0, risk_usdt=0, stop_price=stop_price, r_multiple=r_multiple,
                reject_reason=f"Notional {notional:.2f} < min {min_notional}",
            )
        if notional > max_notional:
            qty = max_notional / entry_price
            notional = max_notional
        if notional > self.config.max_notional_per_symbol_usdt:
            qty = self.config.max_notional_per_symbol_usdt / entry_price
            notional = self.config.max_notional_per_symbol_usdt

        # Round to qty_step
        qty = max(min_qty, round(qty / qty_step) * qty_step)
        notional = qty * entry_price
        risk_usdt = qty * distance

        return PositionSizingResult(
            qty=qty,
            notional_usdt=notional,
            risk_usdt=risk_usdt,
            stop_price=stop_price,
            r_multiple=r_multiple,
        )

    def can_open_position(self, current_positions: int, symbol: str) -> tuple[bool, Optional[str]]:
        """Check if we can open a new position."""
        if self.kill_switch_triggered:
            return False, "Kill switch triggered"
        if current_positions >= self.config.max_concurrent_positions:
            return False, f"Max positions {self.config.max_concurrent_positions}"
        return True, None

    def check_daily_drawdown(self, current_equity: float) -> tuple[bool, Optional[str]]:
        """Check daily drawdown kill switch."""
        if not self.config.kill_switch_enabled or self.daily_pnl_start is None:
            return True, None
        drawdown_pct = (self.daily_pnl_start - current_equity) / self.daily_pnl_start * 100
        if drawdown_pct >= self.config.max_daily_drawdown_pct:
            self.kill_switch_triggered = True
            return False, f"Daily drawdown {drawdown_pct:.2f}% >= {self.config.max_daily_drawdown_pct}%"
        return True, None

    def check_stale_data(self, last_ts_ms: int, now_ms: int) -> tuple[bool, Optional[str]]:
        """Check if data is stale."""
        age_sec = (now_ms - last_ts_ms) / 1000
        if age_sec > self.config.stale_data_seconds:
            return False, f"Data stale: {age_sec:.0f}s"
        return True, None
