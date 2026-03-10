"""Symbol eligibility for entry: universe + context freshness + cooldowns."""

from typing import Optional

from src.data.universe import UniverseManager
from src.data.context_refresher import ContextRefresher
from src.portfolio.position_manager import PositionManager
from src.utils.logging import get_logger

log = get_logger(__name__)


def check_eligibility(
    symbol: str,
    universe: UniverseManager,
    context: ContextRefresher,
    positions: PositionManager,
    now_ms: int,
) -> tuple[bool, str]:
    """
    Check if symbol is eligible for new entry.
    Returns (True, '') or (False, reason).
    """
    if symbol not in universe.symbols:
        return False, "not_in_universe"
    fresh, reason = context.is_symbol_context_fresh(symbol, now_ms)
    if not fresh:
        return False, f"context_stale:{reason}"
    ok, cooldown_reason = positions.can_trade_symbol(symbol, now_ms)
    if not ok:
        return False, cooldown_reason or "cooldown"
    return True, ""
