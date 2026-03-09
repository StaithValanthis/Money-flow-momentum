"""Position manager: track positions, cooldowns, exposure."""

from dataclasses import dataclass, field
from typing import Optional

from src.config.config import RiskConfig
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class TrackedPosition:
    """Tracked open position."""

    symbol: str
    side: str
    size: float
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_ts: int
    order_id: Optional[str] = None


@dataclass
class Cooldown:
    """Track cooldown after loss."""

    symbol: str
    until_ts: int


class PositionManager:
    """Manage open positions and cooldowns."""

    def __init__(self, config: RiskConfig):
        self.config = config
        self._positions: dict[str, TrackedPosition] = {}
        self._cooldowns: dict[str, Cooldown] = {}

    def add_position(self, pos: TrackedPosition) -> None:
        """Add tracked position."""
        self._positions[pos.symbol] = pos

    def remove_position(self, symbol: str) -> Optional[TrackedPosition]:
        """Remove position."""
        return self._positions.pop(symbol, None)

    def get_position(self, symbol: str) -> Optional[TrackedPosition]:
        """Get position for symbol."""
        return self._positions.get(symbol)

    def get_all_positions(self) -> list[TrackedPosition]:
        """Get all positions."""
        return list(self._positions.values())

    def sync_from_exchange(self, positions: list[dict]) -> None:
        """Sync from exchange position list."""
        by_symbol = {p.get("symbol", ""): p for p in positions if p.get("symbol") and float(p.get("size", 0) or 0) > 0}
        for sym, pos in list(self._positions.items()):
            if sym not in by_symbol:
                self._positions.pop(sym, None)
        for sym, ex in by_symbol.items():
            size = float(ex.get("size", 0) or 0)
            if size <= 0:
                continue
            side = "Buy" if float(ex.get("size", 0) or 0) > 0 else "Sell"
            if ex.get("side"):
                side = ex["side"]
            self._positions[sym] = TrackedPosition(
                symbol=sym,
                side=side,
                size=size,
                entry_price=float(ex.get("avgPrice", 0) or 0),
                stop_loss=float(ex.get("stopLoss", 0) or 0),
                take_profit=float(ex.get("takeProfit", 0) or 0),
                entry_ts=int(ex.get("updatedTime", 0) or 0),
            )

    def can_trade_symbol(self, symbol: str, now_ts: int) -> tuple[bool, Optional[str]]:
        """Check if symbol is in cooldown."""
        cd = self._cooldowns.get(symbol)
        if cd and now_ts < cd.until_ts:
            return False, f"Cooldown until {cd.until_ts}"
        return True, None

    def set_cooldown(self, symbol: str, now_ts: int) -> None:
        """Set cooldown after loss."""
        until = now_ts + self.config.cooldown_after_loss_seconds * 1000
        self._cooldowns[symbol] = Cooldown(symbol=symbol, until_ts=until)
        log.info(f"Cooldown set for {symbol} until {until}")

    def count(self) -> int:
        """Current position count."""
        return len(self._positions)
