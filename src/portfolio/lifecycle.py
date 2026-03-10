"""Trade lifecycle state and exit management: TP1/TP2/runner, breakeven, trailing, time stop, flow reversal."""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.config.config import Config, StopTPConfig
from src.data.market_state import MarketStateManager
from src.utils.logging import get_logger

log = get_logger(__name__)


class LifecyclePhase(str, Enum):
    OPEN = "open"
    TP1_FILLED = "tp1_filled"
    TP2_FILLED = "tp2_filled"
    RUNNER = "runner"
    CLOSED = "closed"


@dataclass
class LifecycleState:
    """Per-position lifecycle state."""

    symbol: str
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    atr_at_entry: float
    size: float
    entry_ts: int
    order_link_id: str = ""
    phase: LifecyclePhase = LifecyclePhase.OPEN
    tp1_filled_ts: int = 0
    tp2_filled_ts: int = 0
    stop_moved_to_breakeven: bool = False
    trailing_stop_price: float = 0.0
    exit_reason: str = ""
    # TP1 / TP2 order tracking
    tp1_order_id: str = ""
    tp2_order_id: str = ""
    tp1_planned_qty: float = 0.0
    tp2_planned_qty: float = 0.0
    tp1_filled_qty: float = 0.0
    tp2_filled_qty: float = 0.0


class LifecycleManager:
    """Manage position lifecycle: breakeven move, trailing stop, time stop, flow reversal."""

    def __init__(self, config: Config, market_state: MarketStateManager):
        self.config = config
        self.stop_tp = config.stop_tp
        self.market_state = market_state
        self._states: dict[str, LifecycleState] = {}

    def register(self, state: LifecycleState) -> None:
        """Register new position lifecycle."""
        self._states[state.symbol] = state

    def get(self, symbol: str) -> Optional[LifecycleState]:
        return self._states.get(symbol)

    def remove(self, symbol: str) -> Optional[LifecycleState]:
        return self._states.pop(symbol, None)

    def all_open(self) -> list[LifecycleState]:
        return [s for s in self._states.values() if s.phase != LifecyclePhase.CLOSED]

    def mark_tp1_filled(self, symbol: str, ts: int) -> None:
        s = self._states.get(symbol)
        if s:
            s.tp1_filled_ts = ts
            s.phase = LifecyclePhase.TP1_FILLED

    def mark_tp2_filled(self, symbol: str, ts: int) -> None:
        s = self._states.get(symbol)
        if s:
            s.tp2_filled_ts = ts
            s.phase = LifecyclePhase.TP2_FILLED

    def mark_runner(self, symbol: str) -> None:
        s = self._states.get(symbol)
        if s:
            s.phase = LifecyclePhase.RUNNER

    def mark_stop_at_breakeven(self, symbol: str) -> None:
        s = self._states.get(symbol)
        if s:
            s.stop_moved_to_breakeven = True

    def should_move_to_breakeven(self, symbol: str) -> bool:
        """True if TP1 filled and we have not yet moved stop to breakeven."""
        if not self.stop_tp.breakeven_after_tp1:
            return False
        s = self._states.get(symbol)
        return bool(s and s.phase in (LifecyclePhase.TP1_FILLED, LifecyclePhase.TP2_FILLED, LifecyclePhase.RUNNER) and not s.stop_moved_to_breakeven)

    def breakeven_price(self, symbol: str) -> Optional[float]:
        """Entry price as breakeven."""
        s = self._states.get(symbol)
        return s.entry_price if s else None

    def should_time_stop(self, symbol: str, now_ts: int) -> bool:
        """True if max_hold_seconds exceeded. Stage 4: optional volatility-aware scaling."""
        s = self._states.get(symbol)
        if not s or self.stop_tp.max_hold_seconds <= 0:
            return False
        effective_seconds = self.stop_tp.max_hold_seconds
        if self.stop_tp.volatility_aware_time_stop and self.stop_tp.time_stop_vol_multiplier != 1.0:
            effective_seconds = int(self.stop_tp.max_hold_seconds * self.stop_tp.time_stop_vol_multiplier)
        return (now_ts - s.entry_ts) >= effective_seconds * 1000

    def should_flow_reversal_exit(
        self,
        symbol: str,
        delta_1m: float,
    ) -> bool:
        """True if flow reversed strongly against position."""
        if not self.stop_tp.flow_reversal_exit:
            return False
        s = self._states.get(symbol)
        if not s:
            return False
        threshold = self.stop_tp.flow_reversal_delta_threshold
        if s.side == "Buy" and delta_1m < threshold:
            return True
        if s.side == "Sell" and delta_1m > -threshold:
            return True
        return False

    def trailing_stop_price(self, symbol: str, current_price: float) -> Optional[float]:
        """Compute trailing stop price for runner. Returns None if not in runner phase."""
        s = self._states.get(symbol)
        if not s or s.phase != LifecyclePhase.RUNNER:
            return None
        atr_mult = self.stop_tp.trailing_stop_atr_multiple
        distance = atr_mult * s.atr_at_entry
        if s.side == "Buy":
            trail = current_price - distance
            return max(trail, s.trailing_stop_price) if s.trailing_stop_price else trail
        else:
            trail = current_price + distance
            return min(trail, s.trailing_stop_price) if s.trailing_stop_price else trail

    def update_trailing_stop(self, symbol: str, price: float) -> None:
        s = self._states.get(symbol)
        if s:
            s.trailing_stop_price = price

    def should_exhaustion_exit(
        self,
        symbol: str,
        flow_exhaustion_score: float,
        delta_1m: float,
        side: str,
    ) -> bool:
        """True if flow exhaustion suggests early exit: strong flow but weak price follow-through."""
        if not self.stop_tp.exhaustion_exit_enabled or not self.stop_tp.exhaustion_flow_price_ratio_max:
            return False
        s = self._states.get(symbol)
        if not s:
            return False
        if flow_exhaustion_score < 0.3:
            return False
        if side == "Buy" and delta_1m < 0:
            return True
        if side == "Sell" and delta_1m > 0:
            return True
        return False

    def should_failed_breakout_exit(
        self,
        symbol: str,
        failed_breakout_score: float,
        price_return_1m: float,
        side: str,
    ) -> bool:
        """True if failed breakout (price reversed) suggests exit."""
        if not self.stop_tp.failed_breakout_exit_enabled:
            return False
        s = self._states.get(symbol)
        if not s:
            return False
        if failed_breakout_score < 0.2:
            return False
        rev_pct = self.stop_tp.failed_breakout_reversal_pct
        if side == "Buy" and price_return_1m < -rev_pct:
            return True
        if side == "Sell" and price_return_1m > rev_pct:
            return True
        return False
